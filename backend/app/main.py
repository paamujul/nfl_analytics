"""FastAPI app: the REST API.

Deployment runs this read-only -- DISABLE_INGEST=1 and AUTO_SEED=0 -- with
ingestion handled by scheduled jobs (see app/cli.py). The in-process ingester
and self-seed below exist so local development still works from one command.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


@app.get("/")
def root():
    return {"app": "nfl-analytics", "docs": "/docs", "api": "/api"}
