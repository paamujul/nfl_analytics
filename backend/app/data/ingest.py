"""Live ESPN ingestion: adaptive polling loop + one-shot backfill helpers.

The loop runs inside the FastAPI process (started from the app lifespan).
Every tick it refreshes the current scoreboard; any game that is live — or
final but not yet fully ingested — gets its summary fetched, normalized and
upserted. All writes are idempotent (keyed by game id / play id), so a game
can be re-ingested at any time without duplicating rows.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import (
    NFLVERSE_NIGHTLY, NFLVERSE_REFRESH_SECONDS, POLL_GAMEDAY, POLL_IDLE, POLL_LIVE,
)
from app.data import espn_source as espn
from app.data.seed import start_nflverse_refresh
from app.db.models import Game, Play, Player, PlayerGameStat, SyncLog
from app.db.session import db_session
from app.data.nflverse_source import _upsert_all  # shared sqlite upsert helper

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
        _upsert_all(s, Game, [game])
        _upsert_all(s, Player, players)
        _upsert_all(s, PlayerGameStat, stats)
        _upsert_all(s, Play, plays)
        _log_sync(s, f"game {game['id']}", "ok",
                  rows=len(plays), message=f"status={game['status']}")
    return game["id"]


async def ingest_scoreboard(client: espn.EspnClient, season: int, phase: str,
                            week: int) -> list[dict]:
    payload = await client.scoreboard(season, phase, week)
    games = espn.normalize_scoreboard(payload)
    if games:
        with db_session() as s:
            _upsert_all(s, Game, games)
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


class LiveIngester:
    """Background task: keeps the DB current with whatever ESPN is showing."""

    def __init__(self):
        self._stop = asyncio.Event()
        self.task: asyncio.Task | None = None
        self._last_nflverse: float | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="live-ingester")

    async def stop(self) -> None:
        self._stop.set()
        if self.task:
            self.task.cancel()

    async def run(self) -> None:
        client = espn.EspnClient()
        try:
            while not self._stop.is_set():
                delay = POLL_IDLE
                try:
                    delay = await self._tick(client)
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

    async def _tick(self, client: espn.EspnClient) -> int:
        """One poll cycle; returns the next delay in seconds."""
        # what phase/week is "now"? ask the default scoreboard
        payload = await self._default_scoreboard(client)
        games = espn.normalize_scoreboard(payload)
        if games:
            with db_session() as s:
                _upsert_all(s, Game, games)
        self._maybe_refresh_nflverse(games)

        live = [g for g in games if g["status"] == "in"]
        # refresh live games, and finals that we haven't stored plays for yet
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

        if live:
            return POLL_LIVE
        soon = datetime.now(timezone.utc) + timedelta(hours=4)
        upcoming = [g for g in games if g["status"] == "scheduled"
                    and g.get("kickoff") and g["kickoff"] <= soon.isoformat()]
        return POLL_GAMEDAY if upcoming else POLL_IDLE

    def _maybe_refresh_nflverse(self, games: list[dict]) -> None:
        """Once a day during the regular/post season, pull fresh nflverse data.

        ESPN gives scores and play text live; nflverse adds EPA, success and
        participation a day or so later. Preseason isn't covered by nflverse,
        so only trigger this once real games are being played.
        """
        if not NFLVERSE_NIGHTLY or not games:
            return
        if not any(g["phase"] in ("reg", "post") for g in games):
            return
        now = time.monotonic()
        if self._last_nflverse and now - self._last_nflverse < NFLVERSE_REFRESH_SECONDS:
            return
        self._last_nflverse = now
        start_nflverse_refresh(games[0]["season"])

    @staticmethod
    async def _default_scoreboard(client: espn.EspnClient) -> dict:
        # no params -> ESPN returns the current week's scoreboard
        r = await client._client.get(f"{espn.ESPN_SITE_API}/scoreboard")
        r.raise_for_status()
        return r.json()
