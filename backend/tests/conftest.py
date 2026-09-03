"""Shared fixtures.

Every DB-backed test runs against both backends: SQLite, which local development
uses, and Postgres, which deployment uses. The two disagree in ways that bite --
ON CONFLICT cardinality, VARCHAR length enforcement, and dialect-only functions
like iif() -- and previously nothing in the suite touched Postgres at all.

The Postgres pass is skipped unless TEST_DATABASE_URL is set:

    createdb nfl_analytics_test
    TEST_DATABASE_URL=postgresql+psycopg://localhost/nfl_analytics_test pytest
"""
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.data import espn_source as es
from app.db.models import Base, Game, Play, Player, PlayerGameStat
from app.db.upsert import upsert_all

FIXTURE = Path(__file__).parent / "fixtures" / "espn_summary_401873286.json"

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKENDS = ["sqlite"] + (["postgresql"] if TEST_DATABASE_URL else [])


@pytest.fixture(params=BACKENDS)
def engine(request):
    if request.param == "sqlite":
        # StaticPool keeps the one connection alive so an in-memory database
        # survives across the TestClient's worker thread; check_same_thread
        # off for the same reason.
        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(eng)
        yield eng
        eng.dispose()
        return

    eng = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def session(engine):
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def espn_summary():
    return json.loads(FIXTURE.read_text())


def load_fixture_game(session) -> dict:
    """Ingest the recorded ESPN summary. Returns the normalized game row."""
    summary = json.loads(FIXTURE.read_text())
    game = es.normalize_summary_header(summary)
    players, stats = es.normalize_boxscore(summary, game["id"])
    plays = es.normalize_plays(summary, game["id"], players,
                               game["home_team"], game["away_team"])
    upsert_all(session, Game, [game])
    upsert_all(session, Player, players)
    upsert_all(session, PlayerGameStat, stats)
    upsert_all(session, Play, plays)
    session.commit()
    return game
