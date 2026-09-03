"""Kickoff timestamp normalization.

Game.kickoff is a String column that gets compared with <= to decide whether a
game is starting soon, so the two data sources have to agree on a format. They
did not: ESPN emits Zulu ("2026-09-07T17:00Z") while nflverse builds a bare
local datetime with no offset at all. Since 'Z' (0x5A) sorts above '+' (0x2B),
a Zulu kickoff always compared greater than a '+00:00' bound and the gameday
poll window silently skipped every game.

Everything is normalized to UTC ISO-8601 with an explicit +00:00 offset.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# nflverse publishes gametime as US Eastern wall-clock with no offset.
NFLVERSE_TZ = ZoneInfo("America/New_York")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'. None if unusable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_utc_iso(value: str | None) -> str | None:
    """Normalize any ISO-8601 kickoff to '...+00:00'. Passes None through."""
    dt = parse_iso(value)
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def nflverse_kickoff(gameday: str | None, gametime: str | None) -> str | None:
    """Combine nflverse's date + Eastern wall-clock time into UTC ISO-8601."""
    if not gameday:
        return None
    try:
        naive = datetime.fromisoformat(f"{gameday}T{gametime or '00:00'}")
    except ValueError:
        return None
    return naive.replace(tzinfo=NFLVERSE_TZ).astimezone(timezone.utc).isoformat()
