"""Backfill / maintenance commands.

    python -m app.cli init-db
    python -m app.cli backfill-nflverse 2025
    python -m app.cli backfill-espn 2026 pre
"""
from __future__ import annotations

import asyncio
import sys

from app.db.session import init_db


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    init_db()

    if cmd == "init-db":
        print("database initialized")
    elif cmd == "backfill-nflverse":
        season = int(args[1])
        from app.data.nflverse_source import backfill_season
        out = backfill_season(season)
        print(f"nflverse {season}: {out}")
    elif cmd == "backfill-espn":
        season = int(args[1])
        phase = args[2] if len(args) > 2 else "pre"
        weeks = range(1, 5) if phase == "pre" else range(1, 19) if phase == "reg" else range(1, 5)
        from app.data.ingest import backfill_phase
        out = asyncio.run(backfill_phase(season, phase, weeks))
        print(f"espn {season} {phase}: {out}")
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
