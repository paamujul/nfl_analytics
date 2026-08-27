# Deploying (24/7)

The app has two halves with different hosting needs:

- **Backend** — FastAPI + a background ingestion loop + a SQLite file. It must run
  as an always-on process with a persistent disk. → **Railway** (~$5/month Hobby).
- **Frontend** — a static Vite/React bundle. → **Vercel** (free Hobby tier).

GitHub Pages and Vercel *alone* can't host this: they serve static files and
short-lived serverless functions, neither of which can run a continuous poller or
keep a SQLite file between requests.

---

## 1. Backend on Railway

1. Sign in at [railway.com](https://railway.com) with GitHub.
2. **New Project → Deploy from GitHub repo →** select `paamujul/nfl_analytics`.
3. In the service **Settings**:
   - **Root Directory**: `backend` (it will use `backend/Dockerfile` automatically)
   - **Networking → Generate Domain** — note the resulting
     `https://<something>.up.railway.app` URL.
4. **Add a volume** (service → Variables/Volumes → New Volume):
   - **Mount path**: `/app/storage` — this is where `nfl.db` and the nflverse
     parquet cache live. 2 GB is plenty.
   - Without this, the database resets on every redeploy.
5. Optional environment variables (all have sane defaults):

   | Variable | Default | Purpose |
   |---|---|---|
   | `ALLOWED_ORIGINS` | – | Comma-separated extra CORS origins (set to your Vercel domain; `*.vercel.app` is already allowed) |
   | `AUTO_SEED` | `1` | Seed the database on first boot; set `0` to disable |
   | `NFLVERSE_NIGHTLY` | `1` | Daily nflverse refresh during the season |
   | `DISABLE_INGEST` | – | Set `1` to turn off live polling |

6. Watch the deploy logs. **On first boot the app seeds itself** — ESPN preseason
   first (fast), then the full 2025 nflverse season (a few minutes, ~50k plays).
   Track it at `https://<railway-domain>/api/status`; seeding is done when a
   `seed / startup` entry reads `seeding complete`.

**Memory note:** the nflverse backfill loads large parquet files. Give the service
~2 GB during the initial seed; steady-state usage afterwards is small.

## 2. Frontend on Vercel

1. Sign in at [vercel.com](https://vercel.com) with GitHub.
2. **Add New → Project →** import `paamujul/nfl_analytics`.
3. Set **Root Directory** to `frontend`. The Vite preset is detected
   automatically (`npm run build` → `dist`).
4. Add an environment variable:
   - `VITE_API_BASE` = `https://<your-railway-domain>` (no trailing slash)
5. Deploy. `frontend/vercel.json` handles SPA routing so deep links like
   `/team/KC` load directly.

Then set `ALLOWED_ORIGINS` on Railway to your final Vercel domain and redeploy the
backend.

## 3. Verify it's really running 24/7

```bash
curl https://<railway-domain>/api/status
```

`last_successful_sync` should keep advancing on its own — every ~45 s while a game
is live, ~10 min on game days, hourly otherwise, with your laptop closed.

## Costs

| | Plan | Cost |
|---|---|---|
| Railway | Hobby | ~$5/month (includes $5 usage; this workload sits well inside it) |
| Vercel | Hobby | Free |

## Season rollover (Sept 2026)

Nothing to do. The ingester detects regular-season games on the scoreboard and
starts pulling nflverse data daily, which adds EPA, success rates and
participation on top of the live ESPN scores. Per-play participation (the Defense
lab's play-level combo analysis) publishes after the postseason; until then that
view falls back to snap-count game-level splits and says so.

## Running the backend container locally

```bash
cd backend
docker build -t nfl-analytics-backend .
docker run -p 8600:8600 -v "$(pwd)/storage:/app/storage" nfl-analytics-backend
```
