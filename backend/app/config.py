"""Central configuration for the NFL analytics backend."""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", BACKEND_DIR / "storage"))
CACHE_DIR = STORAGE_DIR / "cache"
DB_PATH = STORAGE_DIR / "nfl.db"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Checked-in seed data that is not downloadable from any upstream feed --
# currently only coaches.yml, since no nflverse dataset carries coordinators.
# Overridable so a container can mount it somewhere other than the repo layout.
DATA_DIR = Path(os.environ.get("DATA_DIR", BACKEND_DIR.parent / "deploy" / "data"))
COACHES_YML = DATA_DIR / "coaches.yml"

# Postgres (Supabase) in deployment via DATABASE_URL; SQLite locally by default.
# Supabase hands out "postgresql://..." URLs, which SQLAlchemy would route to
# psycopg2; normalize onto psycopg3, which is what requirements.txt pins.
DATABASE_URL = os.environ.get("DATABASE_URL") or f"sqlite:///{DB_PATH}"
for _prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_prefix):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(_prefix):]
        break

# Seasons the app knows about. 2026 is the live season (ESPN preseason now,
# nflverse once regular-season data lands); 2021-2025 are complete and are the
# training window for the coaching/play-calling work -- five seasons is the
# shortest span that covers a coordinator's tenure on most staffs.
#
# 2021 is the floor on purpose: FTN charting starts in 2022, and participation's
# pressure/coverage/route columns are only ~39% filled in 2021-22 versus ~100%
# from 2023. Going further back adds rows with almost none of the columns the
# coaching feature is built on.
SEASONS = [2021, 2022, 2023, 2024, 2025, 2026]

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

# Extra browser origins allowed to call the API (comma-separated), e.g. the
# deployed frontend domain. Localhost dev origins are always allowed.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# On first boot with an empty database, seed it in the background so a fresh
# deploy becomes useful without any manual backfill step.
AUTO_SEED = os.environ.get("AUTO_SEED", "1") != "0"
# NB: seeding all six seasons needs roughly 4 GiB of working memory for the
# parquet downloads and lands 350-450 MB in the database. That belongs in a
# Cloud Run Job, not in a web process's background task -- see
# check_storage_budget(), which warns before Supabase's 500 MB read-only cap.
SEED_SEASONS = (2021, 2022, 2023, 2024, 2025, 2026)

