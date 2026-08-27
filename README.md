# NFL Analytics

A live NFL analytics web app: team yardage dashboards, player quarter-by-quarter
breakdowns, route charts, team comparison, defensive identity profiles, and
on-field player-combination impact analysis.

Validated against the completed **2025 season** (nflverse play-by-play), and live
for the **2026 preseason → regular season → postseason** via a background
ingestion service that continuously polls ESPN and persists everything to SQLite —
the frontend is always served from the database, never from a blocking external
API call.

## Features

- **Teams** — every team's total passing / rushing / receiving yards for the
  selected season + phase (preseason, regular season, postseason), per-game trends,
  and full player stat tables.
- **Player pages** — click any player for quarter-by-quarter performance (yards,
  EPA/play, success rate, "best quarter" callout) across a season or a single game.
- **Route charts** — every target a player saw in a game overlaid on a field
  graphic, with frequent + successful zones highlighted, YAC tails, and run-direction
  charts for ball carriers. *Derived from play-by-play outcomes (pass direction,
  air yards, catch point, YAC) — the NFL's tracking data isn't public, and the chart
  says so.*
- **Compare** — two teams side by side, offense and defense, every metric shown
  with its league percentile.
- **Defense lab** — a readable defensive identity ("blitz-heavy, stout run
  defense, vulnerable through the air") computed from EPA allowed, sack/blitz/stuff
  rates, explosive plays allowed, and FTN charting data.
- **Lineup impact** — pick up to 6 defenders: do opponents pass or run more (and
  more efficiently) when that exact group is on the field, vs when it isn't? Same
  question for offense: does the offense lean pass or run with a given group out
  there? Play-level analysis uses nflverse participation (exact on-field players
  per snap); for in-progress seasons it falls back to game-level snap-count presence
  and labels itself accordingly.
- **Live** — a background ingester polls the ESPN scoreboard (every ~45 s during
  live games, slower otherwise), upserts box scores and play-by-play idempotently,
  and the UI shows a live banner. Ingestion health is at `/api/status`.

## Data sources

| Source | Used for |
|---|---|
| [nflverse](https://github.com/nflverse) via `nflreadpy` | Completed regular/post-season weeks: play-by-play (EPA, success, air yards, directions), participation (on-field players per play), snap counts, FTN charting, rosters, schedules, team colors |
| ESPN site API (free, no key) | Preseason + live games: scoreboard, box scores, drives/plays (parsed for player attribution, direction, depth) |

Regular-season game ids match the nflverse convention (`2026_01_KC_LAC`), so rows
from both sources merge cleanly in one store.

## Stack

- **Backend**: FastAPI + SQLAlchemy (SQLite, WAL) + polars/nflreadpy + httpx.
- **Frontend**: React + Vite + TypeScript, Recharts + custom SVG field charts.

## Setup

```bash
# backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# load the 2025 season (validation baseline) and the 2026 preseason
.venv/bin/python -m app.cli backfill-nflverse 2025
.venv/bin/python -m app.cli backfill-espn 2026 pre

# run the API + live ingester
.venv/bin/python -m uvicorn app.main:app --port 8600
```

```bash
# frontend (separate terminal)
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api to :8600)
```

Once the 2026 regular season starts, re-run
`python -m app.cli backfill-nflverse 2026` periodically (or nightly via cron) for
the deep analytics; ESPN live ingestion keeps scores/box scores current on its own.

## Tests

```bash
cd backend && .venv/bin/python -m pytest tests -q
```

Includes: ESPN normalizer checks against a recorded 2026 preseason fixture,
upsert idempotency (re-ingesting a game changes nothing), and 2025 validation —
play-by-play-derived team passing yards must agree with official box-score totals
within 3% for all 32 teams, and quarter splits must sum to player season totals.

## Honest limitations

- Route charts are reconstructions from play outcomes, not player tracking.
- Preseason plays carry no EPA (no public model outputs for preseason), so those
  views show yardage-based metrics only.
- Per-play participation publishes after the postseason; in-season combo analysis
  is game-level via snap counts and the UI labels it as such.
