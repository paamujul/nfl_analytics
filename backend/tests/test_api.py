"""Endpoint smoke tests: every route must execute its SQL on both backends.

The point is dialect coverage, not analytics correctness. Nothing previously
exercised the API layer, which is how func.iif() -- SQLite-only, in the one
endpoint every page load hits -- sat in /api/seasons undetected.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.db.session import get_db
from tests.conftest import load_fixture_game


@pytest.fixture()
def client(engine, session):
    game = load_fixture_game(session)
    # plain TestClient (not a context manager) so the app lifespan -- and with
    # it the self-seed and the live ingester -- never starts
    from app.main import app

    Testing = sessionmaker(bind=engine)

    def override_get_db():
        # must be a generator like the real dependency: a plain factory leaves
        # every request's session open, which deadlocks the Postgres teardown
        s = Testing()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app)
    c.game = game
    yield c
    app.dependency_overrides.clear()


def test_seasons(client):
    """Regression: this endpoint used SQLite-only iif() and 500'd on Postgres."""
    body = client.get("/api/seasons").json()
    assert body and body[0]["phases"]
    assert all(isinstance(p["played"], int) for s in body for p in s["phases"])


def test_teams_and_team_detail(client):
    g = client.game
    q = {"season": g["season"], "phase": g["phase"]}
    assert client.get("/api/teams", params=q).status_code == 200

    for team in (g["home_team"], g["away_team"]):
        assert client.get(f"/api/teams/{team}", params=q).status_code == 200
        assert client.get(f"/api/teams/{team}/defense", params=q).status_code == 200
        r = client.get(f"/api/teams/{team}/roster", params={**q, "side": "offense"})
        assert r.status_code == 200


def test_lineup_impact(client):
    g = client.game
    q = {"season": g["season"], "phase": g["phase"], "side": "defense"}
    roster = client.get(f"/api/teams/{g['home_team']}/roster", params=q).json()
    ids = [p["player_id"] for p in roster][:2] or ["espn:0"]
    r = client.get(f"/api/teams/{g['home_team']}/lineup-impact",
                   params={**q, "players": ",".join(ids)})
    assert r.status_code == 200


def test_player_endpoints(client):
    g = client.game
    roster = client.get(f"/api/teams/{g['home_team']}/roster",
                        params={"season": g["season"], "phase": g["phase"],
                                "side": "offense"}).json()
    pid = roster[0]["player_id"] if roster else "espn:0"
    r = client.get(f"/api/players/{pid}/quarters",
                   params={"season": g["season"], "phase": g["phase"]})
    assert r.status_code == 200
    assert client.get(f"/api/players/{pid}/routes",
                      params={"game": g["id"]}).status_code == 200


def test_compare(client):
    g = client.game
    r = client.get("/api/compare", params={
        "teamA": g["home_team"], "teamB": g["away_team"],
        "season": g["season"], "phase": g["phase"]})
    assert r.status_code == 200


def test_live_and_status(client):
    assert client.get("/api/live").status_code == 200
    body = client.get("/api/status").json()
    assert body["games_in_db"] == 1
    assert "recent" in body


def test_health_reports_stale_when_nothing_has_synced(client):
    """No successful sync yet -> 503, so UptimeRobot catches a wedged ingester."""
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json()["status"] == "stale"


def test_health_ok_after_a_successful_sync(client, session):
    from app.db.models import SyncLog
    session.add(SyncLog(source="espn", scope="tick", status="ok"))
    session.commit()
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
