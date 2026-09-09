"""Live ESPN ingestion: one poll cycle, plus one-shot backfill helpers.

poll_once() refreshes the current scoreboard and fetches any game that is live,
or final but not yet fully ingested. All writes are idempotent (keyed by game id
/ play id), so a cycle can be repeated at any time without duplicating rows.

The VM deployment runs LiveIngester in the API process, the same way local
development does; `python -m app.cli poll-once` still runs exactly one cycle
for a one-shot or scheduled invocation.

Every DB block here goes through asyncio.to_thread. The calls are synchronous
psycopg and poll_once() is driven from the API process's event loop, so left
inline they run *on* the loop thread: thirteen live games on a Sunday is 50+
cross-cloud round trips per cycle, during which uvicorn can neither accept
connections nor dispatch to the worker threadpool. Every 45 seconds, while
games are on, which is exactly when traffic peaks. The httpx fetches are
already async and stay on the loop.
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


# --- sync DB blocks, each called via asyncio.to_thread from the async layer ---
# Kept as plain functions rather than inline lambdas so the transaction boundary
# (one db_session() == one commit) stays visible at a glance.

def _write_sync_log(scope: str, status: str, message: str | None = None,
                    rows: int | None = None) -> None:
    with db_session() as s:
        _log_sync(s, scope, status, message=message, rows=rows)


def _write_game(game: dict, players: list[dict], stats: list[dict],
                plays: list[dict]) -> None:
    with db_session() as s:
        upsert_all(s, Game, [game])
        upsert_all(s, Player, players)
        upsert_all(s, PlayerGameStat, stats)
        upsert_all(s, Play, plays)
        _log_sync(s, f"game {game['id']}", "ok",
                  rows=len(plays), message=f"status={game['status']}")


def _write_games(games: list[dict], scope: str | None = None) -> None:
    with db_session() as s:
        upsert_all(s, Game, games)
        if scope:
            _log_sync(s, scope, "ok", rows=len(games))


def _finals_needing_fetch(games: list[dict]) -> set[str]:
    """ESPN event ids for finals we have not stored a full set of plays for."""
    out: set[str] = set()
    with db_session() as s:
        for g in games:
            if g["status"] == "final":
                row = s.get(Game, g["id"])
                if row is None or not _game_fully_ingested(s, row):
                    out.add(g["espn_event_id"])
    return out


async def ingest_game(client: espn.EspnClient, event_id: str) -> str | None:
    """Fetch + persist one game's summary. Returns the game id."""
    summary = await client.summary(event_id)
    game = espn.normalize_summary_header(summary)
    if game is None:
        return None
    players, stats = espn.normalize_boxscore(summary, game["id"])
    plays = espn.normalize_plays(summary, game["id"], players,
                                 game["home_team"], game["away_team"])
    await asyncio.to_thread(_write_game, game, players, stats, plays)
    return game["id"]


async def ingest_scoreboard(client: espn.EspnClient, season: int, phase: str,
                            week: int) -> list[dict]:
    payload = await client.scoreboard(season, phase, week)
    games = espn.normalize_scoreboard(payload)
    if games:
        await asyncio.to_thread(_write_games, games,
                                f"scoreboard {season} {phase} w{week}")
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
                    await asyncio.to_thread(_write_sync_log, f"game {g['id']}",
                                            "error", str(e))
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
        await asyncio.to_thread(_write_games, games)

    live = [g for g in games if g["status"] == "in"]
    # refresh live games, and finals we haven't stored plays for yet
    to_fetch = {g["espn_event_id"] for g in live}
    to_fetch |= await asyncio.to_thread(_finals_needing_fetch, games)
    for event_id in to_fetch:
        try:
            await ingest_game(client, event_id)
        except Exception as e:
            log.warning("live ingest %s failed: %s", event_id, e)

    # record the cycle itself, not just per-game ingests: most of the year
    # there is nothing to fetch, and /api/health needs to tell "ran fine, no
    # games on" apart from "the scheduled job stopped running".
    await asyncio.to_thread(_write_sync_log, "poll", "ok",
                            f"{len(live)} live, {len(to_fetch)} fetched",
                            len(games))

    if live:
        return POLL_LIVE
    soon = datetime.now(timezone.utc) + timedelta(hours=4)
    upcoming = [g for g in games if g["status"] == "scheduled"
                and (kick := parse_iso(g.get("kickoff"))) and kick <= soon]
    return POLL_GAMEDAY if upcoming else POLL_IDLE


class LiveIngester:
    """Background task in the API process: keeps the DB current with ESPN.

    The VM deployment runs this, the same as local development does. It was
    disabled under Cloud Run (DISABLE_INGEST=1), where short-lived instances
    made an in-process poll loop meaningless and Cloud Scheduler drove
    poll_once instead.
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
                    await asyncio.to_thread(_write_sync_log, "tick", "error", str(e))
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            await client.close()
