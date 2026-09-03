"""Live ESPN ingestion: one poll cycle, plus one-shot backfill helpers.

poll_once() refreshes the current scoreboard and fetches any game that is live,
or final but not yet fully ingested. All writes are idempotent (keyed by game id
/ play id), so a cycle can be repeated at any time without duplicating rows.

Deployment runs poll_once as a scheduled one-shot job (`python -m app.cli
poll-once`) and never starts LiveIngester, which exists so `uvicorn app.main:app`
still self-updates during local development.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import POLL_GAMEDAY, POLL_IDLE, POLL_LIVE
from app.data import espn_source as espn
from app.data.timeutil import parse_iso
from app.db.models import Game, Play, Player, PlayerGameStat, SyncLog
from app.db.session import db_session
from app.db.upsert import upsert_all

log = logging.getLogger("ingest")


def _log_sync(session, scope: str, status: str, message: str | None = None, rows: int | None = None):
    session.add(SyncLog(source="espn", scope=scope, status=status, message=message, rows=rows))


async def ingest_game(client: espn.EspnClient, event_id: str) -> str | None:
    """Fetch + persist one game's summary. Returns the game id."""
    summary = await client.summary(event_id)
    game = espn.normalize_summary_header(summary)
    if game is None:
        return None
    players, stats = espn.normalize_boxscore(summary, game["id"])
    plays = espn.normalize_plays(summary, game["id"], players,
                                 game["home_team"], game["away_team"])
    with db_session() as s:
        upsert_all(s, Game, [game])
        upsert_all(s, Player, players)
        upsert_all(s, PlayerGameStat, stats)
        upsert_all(s, Play, plays)
        _log_sync(s, f"game {game['id']}", "ok",
                  rows=len(plays), message=f"status={game['status']}")
    return game["id"]


async def ingest_scoreboard(client: espn.EspnClient, season: int, phase: str,
                            week: int) -> list[dict]:
    payload = await client.scoreboard(season, phase, week)
    games = espn.normalize_scoreboard(payload)
    if games:
        with db_session() as s:
            upsert_all(s, Game, games)
            _log_sync(s, f"scoreboard {season} {phase} w{week}", "ok", rows=len(games))
    return games


async def backfill_phase(season: int, phase: str, weeks: range) -> dict[str, int]:
    """Ingest every completed game of a phase (e.g. all 2026 preseason weeks)."""
    client = espn.EspnClient()
    done = 0
    seen = 0
    try:
        for week in weeks:
            try:
                games = await ingest_scoreboard(client, season, phase, week)
            except Exception as e:
                log.warning("scoreboard %s %s w%s failed: %s", season, phase, week, e)
                continue
            seen += len(games)
            for g in games:
                if g["status"] == "scheduled":
                    continue
                try:
                    await ingest_game(client, g["espn_event_id"])
                    done += 1
                except Exception as e:
                    log.warning("game %s failed: %s", g["id"], e)
                    with db_session() as s:
                        _log_sync(s, f"game {g['id']}", "error", message=str(e))
    finally:
        await client.close()
    return {"games_seen": seen, "games_ingested": done}


def _game_fully_ingested(session, game: Game) -> bool:
    if game.status != "final":
        return False
    n = session.scalar(select(func.count()).select_from(Play).where(Play.game_id == game.id))
    return bool(n and n > 20)


async def scoreboard_now(client: espn.EspnClient) -> dict:
    """The current week's scoreboard -- ESPN infers season/phase/week with no params."""
    return await client.get_json(f"{espn.ESPN_SITE_API}/scoreboard")


async def poll_once(client: espn.EspnClient) -> int:
    """One poll cycle. Returns how many seconds to wait before the next one.

    Module-level and stateless so a scheduled job can run exactly one cycle and
    exit; LiveIngester.run() loops over it for local development.
    """
    payload = await scoreboard_now(client)
    games = espn.normalize_scoreboard(payload)
    if games:
        with db_session() as s:
            upsert_all(s, Game, games)

    live = [g for g in games if g["status"] == "in"]
    # refresh live games, and finals we haven't stored plays for yet
    to_fetch = {g["espn_event_id"] for g in live}
    with db_session() as s:
        for g in games:
            if g["status"] == "final":
                row = s.get(Game, g["id"])
                if row is None or not _game_fully_ingested(s, row):
                    to_fetch.add(g["espn_event_id"])
    for event_id in to_fetch:
        try:
            await ingest_game(client, event_id)
        except Exception as e:
            log.warning("live ingest %s failed: %s", event_id, e)

    # record the cycle itself, not just per-game ingests: most of the year
    # there is nothing to fetch, and /api/health needs to tell "ran fine, no
    # games on" apart from "the scheduled job stopped running".
    with db_session() as s:
        _log_sync(s, "poll", "ok", rows=len(games),
                  message=f"{len(live)} live, {len(to_fetch)} fetched")

    if live:
        return POLL_LIVE
    soon = datetime.now(timezone.utc) + timedelta(hours=4)
    upcoming = [g for g in games if g["status"] == "scheduled"
                and (kick := parse_iso(g.get("kickoff"))) and kick <= soon]
    return POLL_GAMEDAY if upcoming else POLL_IDLE


class LiveIngester:
    """Local-development background task: keeps the DB current with ESPN.

    Deployment does not use this -- Cloud Scheduler drives poll_once instead.
    """

    def __init__(self):
        self._stop = asyncio.Event()
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="live-ingester")

    async def stop(self) -> None:
        self._stop.set()
        if not self.task:
            return
        # wait for the current cycle to finish rather than cancelling into an
        # open transaction, which strands a server-side connection on Postgres
        try:
            await asyncio.wait_for(self.task, timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.task.cancel()

    async def run(self) -> None:
        client = espn.EspnClient()
        try:
            while not self._stop.is_set():
                delay = POLL_IDLE
                try:
                    delay = await poll_once(client)
                except Exception as e:
                    log.warning("ingest tick failed: %s", e)
                    with db_session() as s:
                        _log_sync(s, "tick", "error", message=str(e))
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            await client.close()
