# Build context is the REPO ROOT, not backend/ -- the SPA is baked into this
# image so FastAPI can serve it same-origin. Build with:
#   docker build -f backend/Dockerfile -t <image> .
# Every COPY below is therefore prefixed with backend/ or frontend/, and the
# root .dockerignore (not backend/.dockerignore) is the one that applies.

# --- SPA build stage --------------------------------------------------------
# Node 22 is pinned by frontend/.nvmrc and required by Vite 8. The SPA is built
# here, in CI, rather than on the VM: `npm ci && vite build` peaks well over
# 1 GB and the target VM has 1-2 GB total.
FROM node:22-slim AS web
WORKDIR /web

# package*.json first so the dependency layer caches independently of source.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Deliberately NO VITE_API_BASE. The bundle is served by the same FastAPI
# process that serves /api, so src/api.ts's empty-string default (same-origin)
# is correct. Setting a base here would hard-code a hostname into the bundle.
RUN npm run build

# --- Runtime stage ----------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini .

# The built SPA: /app/static/index.html plus hashed assets in /app/static/assets.
# app.main mounts this at / and serves index.html for unknown paths so client
# -side routes survive a hard refresh. Copied before the chown below so the
# non-root user can read it.
COPY --from=web /web/dist ./static

# app.config mkdirs BOTH STORAGE_DIR and CACHE_DIR at import time (config.py
# lines 10-11), before anything gets a chance to handle the error. If either is
# missing and its parent is not writable by the running user, `import
# app.config` raises PermissionError -- which kills app.main, app.cli and
# alembic/env.py alike, and does it with a traceback pointing at a mkdir call
# rather than at the actual permissions problem. So create both here and hand
# them to the runtime user; a bind-mounted host volume over /app/storage must
# likewise be writable by uid 10001.
ENV STORAGE_DIR=/app/storage
ENV STATIC_DIR=/app/static
RUN useradd --system --create-home --uid 10001 nflapp \
    && mkdir -p "$STORAGE_DIR/cache" \
    && chown -R nflapp:nflapp /app
USER nflapp

# 8600 is the default; PORT still wins so the nflverse refresh Job (which is
# the one thing left on Cloud Run) keeps working. Jobs override the command
# entirely (python -m app.cli refresh-nflverse, seed).
#
# `exec` is load-bearing, not style. Without it PID 1 stays `sh` (verified:
# /proc/1/cmdline read back as `sh -c uvicorn ...`, so dash does NOT fold this
# into an exec -- the ${PORT} expansion defeats that optimisation). Docker sends
# SIGTERM to PID 1 and sh does not forward it, so uvicorn would never shut down
# gracefully: LiveIngester.stop() (ingest.py:165) would never get to drain the
# in-flight cycle, and every deploy would SIGKILL mid-transaction. The
# --stop-timeout 45 / TimeoutStopSec=60 nesting in deploy/gcp/ depends on this.
#
# --proxy-headers/--forwarded-allow-ips=* lets uvicorn trust X-Forwarded-*.
# Safe here because the container publishes only on the VM's loopback and the
# sole client is cloudflared on that same host -- nothing untrusted can reach
# the socket to forge those headers.
EXPOSE 8600
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8600} --proxy-headers --forwarded-allow-ips=*"]
