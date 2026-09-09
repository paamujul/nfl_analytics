"""FastAPI app: the REST API, and in deployment the SPA that talks to it.

The VM serves both from one origin: the built frontend is baked into the image
at /app/static (override with STATIC_DIR) and mounted below. On a dev machine
that directory does not exist, the mount is skipped entirely, and Vite keeps
serving the SPA on :5173 against this process's API as before.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import ALLOWED_ORIGINS, AUTO_SEED
from app.data.ingest import LiveIngester
from app.data.seed import start_seed_if_needed
from app.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if AUTO_SEED:
        start_seed_if_needed()
    ingester = None
    if os.environ.get("DISABLE_INGEST") != "1":
        ingester = LiveIngester()
        ingester.start()
    yield
    if ingester:
        await ingester.stop()


app = FastAPI(title="NFL Analytics", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", *ALLOWED_ORIGINS],
    # Cloudflare Pages and Vercel both give every preview deploy its own
    # subdomain, so these have to be matched by pattern, not listed.
    allow_origin_regex=r"https://.*\.(pages\.dev|vercel\.app)",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/")
def api_root():
    # Was mounted at "/" -- moved so it does not shadow the SPA index below.
    return {"app": "nfl-analytics", "docs": "/docs", "api": "/api"}


# --- SPA, when it has been built into the image (see backend/Dockerfile) ---

STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))


class ImmutableStatic(StaticFiles):
    """StaticFiles that pins fingerprinted bundles forever.

    Mirrors frontend/public/_headers, which is what Cloudflare Pages used to
    read. Serving these ourselves, nothing sets the header, and Cloudflare
    falls back to its default TTL on files whose names already encode their
    content -- which gives away most of the egress win of putting a CDN in
    front of the VM at all.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if (STATIC_DIR / "index.html").exists():
    # StaticFiles raises at construction on a missing directory, so check
    # first rather than crashing the process on a half-built image.
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", ImmutableStatic(directory=STATIC_DIR / "assets"),
                  name="assets")

    # Registered after include_router on purpose: FastAPI matches in
    # registration order, so a catch-all declared any earlier swallows every
    # /api/* request before the router ever sees it. /docs, /redoc and
    # /openapi.json are registered by the FastAPI() constructor above, so they
    # win over this too.
    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            # Otherwise an unknown API path returns the HTML shell with a 200,
            # and every client-side API typo shows up as a JSON parse error
            # instead of the 404 it is.
            raise HTTPException(status_code=404)

        candidate = (STATIC_DIR / full_path).resolve()
        root = STATIC_DIR.resolve()
        # Traversal guard: "../.." in the path must not escape STATIC_DIR.
        if full_path and root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)

        # Anything else is a client-side route: hand back the shell. no-cache,
        # never immutable -- a cached shell leaves clients on bundles a deploy
        # has already deleted.
        return FileResponse(STATIC_DIR / "index.html",
                            headers={"Cache-Control": "no-cache"})
