"""Backfill / maintenance commands. Also the entrypoint for scheduled jobs.

    python -m app.cli init-db                 create tables (local SQLite only)
    python -m app.cli seed                    full first-time load
    python -m app.cli poll-once               one live ESPN cycle, then exit
    python -m app.cli refresh-nflverse 2026   nflverse refresh + sync_log prune
    python -m app.cli backfill-nflverse 2025  one season from nflverse
    python -m app.cli backfill-espn 2026 pre  one ESPN phase (pre|reg|post)

Every command exits non-zero when the work actually failed, so Cloud Scheduler
and CI report red instead of silently succeeding at nothing.
"""
from __future__ import annotations

import asyncio
import logging
import sys

PHASE_WEEKS = {"pre": range(1, 5), "reg": range(1, 19), "post": range(1, 5)}


def _arg_int(args: list[str], i: int, name: str) -> int:
    try:
        return int(args[i])
    except (IndexError, ValueError):
        raise SystemExit(f"{name} is required and must be an integer")


def _cmd_seed() -> int:
    from app.data.seed import _seed
    _seed()
    return 0


def _cmd_poll_once() -> int:
    from app.data import espn_source as espn
    from app.data.ingest import poll_once

    async def run() -> int:
        client = espn.EspnClient()
        try:
            return await poll_once(client)
        finally:
            await client.close()

    delay = asyncio.run(run())
    print(f"poll complete; next cycle would be in {delay}s")
    return 0


def _cmd_refresh_nflverse(season: int) -> int:
    from app.data.seed import prune_sync_log, refresh_nflverse
    failed = refresh_nflverse(season)
    print(f"nflverse refresh {season}: {failed} step(s) failed")
    print(f"pruned {prune_sync_log()} sync_log row(s)")
    return 1 if failed else 0


def _cmd_backfill_nflverse(season: int) -> int:
    from app.data.nflverse_source import backfill_season
    out = backfill_season(season)
    print(f"nflverse {season}: {out}")
    return 1 if out.get("failed") else 0


def _cmd_backfill_espn(season: int, phase: str) -> int:
    if phase not in PHASE_WEEKS:
        raise SystemExit(f"phase must be one of {sorted(PHASE_WEEKS)}, got {phase!r}")
    from app.data.ingest import backfill_phase
    out = asyncio.run(backfill_phase(season, phase, PHASE_WEEKS[phase]))
    print(f"espn {season} {phase}: {out}")
    # seeing games but storing none is a real failure; seeing none is just an
    # unplayed phase (post-season weeks don't exist until January)
    return 1 if out["games_seen"] and not out["games_ingested"] else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd, rest = args[0], args[1:]

    if cmd == "init-db":
        from app.db.session import init_db
        init_db()
        print("database initialized")
        return

    handlers = {
        "seed": lambda: _cmd_seed(),
        "poll-once": lambda: _cmd_poll_once(),
        "refresh-nflverse": lambda: _cmd_refresh_nflverse(_arg_int(rest, 0, "season")),
        "backfill-nflverse": lambda: _cmd_backfill_nflverse(_arg_int(rest, 0, "season")),
        "backfill-espn": lambda: _cmd_backfill_espn(
            _arg_int(rest, 0, "season"), rest[1] if len(rest) > 1 else "pre"),
    }
    handler = handlers.get(cmd)
    if handler is None:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)

    try:
        sys.exit(handler())
    except SystemExit:
        raise
    except Exception as e:
        logging.getLogger("cli").exception("%s failed", cmd)
        sys.exit(f"{cmd} failed: {e}")


if __name__ == "__main__":
    main()
