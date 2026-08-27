"""FastAPI app: REST API + background live-ingestion service."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.data.ingest import LiveIngester
from app.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {"app": "nfl-analytics", "docs": "/docs", "api": "/api"}
