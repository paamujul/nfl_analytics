# Deploying free & 24/7 on Cloud Run + Cloudflare Pages

This is the **primary** deployment path: the API on Cloud Run scaling to zero,
ingestion as scheduled one-shot jobs, data in Supabase Postgres, the frontend on
Cloudflare Pages. Everything sits inside permanent free tiers.

## Why this shape

The app used to be one always-on process: FastAPI, a live ESPN poller inside its
lifespan, and a SQLite file on local disk. That forced always-on hosting, which
is why the other two guides exist.

But the real duty cycle is about **6%** — NFL games occupy roughly 20 hours a
week for 22 weeks a year. So the poller was split out of the web process into a
job that runs one cycle and exits (`python -m app.cli poll-once`), and SQLite was
replaced with managed Postgres so the API holds no state and can be killed and
replaced freely.

**Trade-off:** live scores lag up to 10 minutes rather than 45 seconds.

| Layer | Service | Cost |
|---|---|---|
| Frontend | Cloudflare Pages | $0 |
| API | Cloud Run, scale-to-zero | $0 (free tier) |
| Ingester + nflverse refresh | Cloud Run Jobs + Cloud Scheduler | $0 |
| Database | Supabase Postgres | $0 |
| Monitoring | UptimeRobot | $0 |

## This deployment

| | |
|---|---|
| API | `https://nfl-analytics-api-299027847230.us-east1.run.app` |
| GCP project / region | `project-e7b849a1-fc6a-41d7-879` / `us-east1` |
| Database | Supabase `dnwdasegtdejtjgexmku`, AWS `us-east-2` |
| Image | `us-east1-docker.pkg.dev/project-e7b849a1-fc6a-41d7-879/nfl-analytics/api` |

Measured cold start is ~0.8s, so there is no reason to pay for `min-instances`.
Build for `linux/amd64` explicitly if you are on an Apple Silicon Mac.

---

## 1. Supabase

Create a free project. Two connection strings matter and they are **not**
interchangeable:

| Use | Connection | Host / port |
|---|---|---|
| Alembic migrations, `psql` | **Session pooler** | `*.pooler.supabase.com:5432` |
| Cloud Run service and jobs | **Transaction pooler** | `*.pooler.supabase.com:6543` |

Use the *pooler* host for both, not `db.<ref>.supabase.co`. Supabase's direct
endpoint is IPv6-only and does not resolve from most networks — the session
pooler on 5432 is its IPv4 substitute and runs DDL fine.

Transaction-mode pooling (6543) can't run DDL and can't hold server-side
prepared statements. The app already disables those (`prepare_threshold=None` in
[backend/app/db/session.py](backend/app/db/session.py)) — without it you get
intermittent `prepared statement already exists` errors once a query has run
five times.

Store the runtime (6543) URL in Secret Manager as `nfl-database-url`. Paste
Supabase's `postgresql://...` string as-is; `app/config.py` rewrites the scheme
onto psycopg3.

The free tier pauses only after **7 days of zero activity**, which the
10-minute poll job makes unreachable.

## 2. Create the schema and seed

```bash
DATABASE_URL='<session pooler 5432 url>' alembic upgrade head
```

Then seed once. The data is fully reproducible from nflverse and ESPN, so there
is nothing to migrate out of the old SQLite file:

```bash
DATABASE_URL='<session pooler 5432 url>' python -m app.cli seed
```

Takes several minutes (~50k plays, several hundred MB of parquet). Needs ~2 GB of
memory, so run it locally or as a one-off 2 GB Cloud Run Job execution — not on
the API service. Watch `/api/status` for `seeding complete`.

## 3. Cloud Run service (API)

| Setting | Value |
|---|---|
| Memory / CPU | 512 MiB, 1 vCPU |
| Billing | request-based (do **not** pass `--no-cpu-throttling`) |
| Instances | min 0, max 3 |
| Concurrency | 80 |
| Timeout | 60s |

Env: `DISABLE_INGEST=1`, `AUTO_SEED=0`, `ALLOWED_ORIGINS=https://<your>.pages.dev`,
and `DATABASE_URL` from Secret Manager. The first two are what keep the
in-process poller and self-seed — which exist for local development — from
starting on a container that gets frozen between requests.

`.github/workflows/deploy.yml` does all of this on push to `main`.

## 4. Cloud Run Jobs + Scheduler

Same image, different command.

| Job | Command | Memory | Timeout | Schedule |
|---|---|---|---|---|
| `nfl-analytics-poll` | `python -m app.cli poll-once` | 512 MiB | 300s | `*/10 * * * *` |
| `nfl-analytics-nflverse-refresh` | `python -m app.cli refresh-nflverse 2026` | **2 GiB** | 1800s | `0 9 * * *` |

The 2 GiB is not optional — the nflverse backfill loads season parquet and gets
OOM-killed at 1 GB.

Run `poll` every 10 minutes year-round rather than encoding the NFL calendar:
flex scheduling moves games, international kickoffs are at 09:30 ET, and playoff
dates float. It exits in ~3 seconds when nothing is live, so a full month is
roughly 6,500 GiB-seconds against a 360,000 free allowance.

Both commands exit non-zero when the work actually failed, so a red Scheduler
run means something.

## 5. Cloudflare Pages

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Build command | `npm run build` |
| Output directory | `dist` |
| `VITE_API_BASE` | the Cloud Run service URL, no trailing slash |

Node version comes from [frontend/.nvmrc](frontend/.nvmrc); Pages' default is too
old for Vite 8. SPA routing and cache policy ship as
`frontend/public/_redirects` and `frontend/public/_headers`.

`npm run build` runs `tsc -b` first with `noUnusedLocals` on, so an unused import
fails the deploy.

## 6. Monitoring

Point UptimeRobot at:

- `GET /` — liveness, touches no database.
- `GET /api/health` — returns **503** once the last successful sync is more than
  six hours old. This is the one worth alerting on: it catches a wedged or
  unscheduled ingester, which a plain 200 check never would. `poll-once` writes
  an `ok` row every cycle, so it stays green through the offseason.

## 7. Confirm it's actually free

The binding Cloud Run limit is **360,000 GiB-seconds/month** (also 180,000
vCPU-seconds and 2M requests). Two things to keep an eye on:

- **Egress is only 1 GB/month free in North America.** The CDN serves all static
  assets so the API emits JSON only, but verify against real traffic.
- **Set a GCP budget alert at $1.** Cloud Run has no hard spend cap.

## Verification

```bash
curl -s https://<cloud-run-url>/api/health
```

1. `/api/health` returns 200 with a recent `last_successful_sync`.
2. All five frontend routes load from the Pages URL with no CORS errors, and a
   cold start (first hit after ~20 minutes idle) is under ~3 seconds.
3. Force-run the `poll` job; it exits 0 and `/api/status` shows a fresh sync.
4. Force-run `nflverse-refresh`; it finishes inside the timeout without OOM.
5. Break the database secret on purpose; `/api/health` goes 503 and UptimeRobot
   alerts.
6. After 48 hours, check Cloud Run metrics against the free-tier limits.

## Season rollover

Nothing to do beyond bumping the season argument on the `nflverse-refresh` job
once 2027 starts. The ESPN poller picks up phase changes on its own.
