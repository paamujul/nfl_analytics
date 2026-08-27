"""Central configuration for the NFL analytics backend."""
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BACKEND_DIR / "storage"
CACHE_DIR = STORAGE_DIR / "cache"
DB_PATH = STORAGE_DIR / "nfl.db"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_PATH}"

# Seasons the app knows about. 2025 is the validation season (nflverse),
# 2026 is the live season (ESPN preseason now, nflverse once reg season data lands).
SEASONS = [2025, 2026]

# Phase identifiers used across the app. ESPN "seasontype": 1=pre, 2=reg, 3=post.
PHASES = {"pre": 1, "reg": 2, "post": 3}
PHASE_FROM_ESPN = {1: "pre", 2: "reg", 3: "post"}

# nflverse game_type -> phase ("REG" and the various post-season rounds)
PHASE_FROM_NFLVERSE = {
    "REG": "reg",
    "WC": "post",
    "DIV": "post",
    "CON": "post",
    "SB": "post",
    "POST": "post",
}

# ESPN team abbreviations that differ from nflverse's
ESPN_TO_NFLVERSE_TEAM = {"WSH": "WAS", "LAR": "LA"}

ESPN_SITE_API = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

# Live ingestion cadence (seconds)
POLL_LIVE = 45          # while at least one game is in progress
POLL_GAMEDAY = 10 * 60  # a game is scheduled within the next few hours
POLL_IDLE = 60 * 60     # off day

# Minimum plays on/off the field for a lineup-impact split to be reported
LINEUP_MIN_PLAYS = 20
