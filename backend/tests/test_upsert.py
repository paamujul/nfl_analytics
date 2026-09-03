"""Upsert semantics: re-ingesting the same game must change nothing."""
from sqlalchemy import func, select

from app.db.models import Game, Play, Player, PlayerGameStat
from app.db.upsert import upsert_all
from tests.conftest import load_fixture_game as _ingest


def _counts(session):
    return tuple(session.scalar(select(func.count()).select_from(t))
                 for t in (Game, Player, PlayerGameStat, Play))


def test_double_ingest_is_idempotent(session):
    _ingest(session)
    first = _counts(session)
    assert first[0] == 1 and all(n > 0 for n in first)

    _ingest(session)
    assert _counts(session) == first

    # spot-check a value survived the second pass intact
    row = session.scalars(select(PlayerGameStat).where(PlayerGameStat.pass_att > 20)).first()
    assert row is not None and row.pass_yds > 0


def test_heterogeneous_rows_keep_all_columns(session):
    """Rows with different key sets must not lose columns (kickoff-first bug)."""
    upsert_all(session, Game, [{
        "id": "2026_P01_A_B", "season": 2026, "phase": "pre", "week": 1,
        "home_team": "B", "away_team": "A", "status": "final", "source": "espn",
    }])
    rows = [
        {"game_id": "2026_P01_A_B", "play_id": 1, "play_type": "kickoff"},
        {"game_id": "2026_P01_A_B", "play_id": 2, "play_type": "pass",
         "passer_id": "espn:1", "receiver_id": "espn:2", "yards_gained": 12.0},
    ]
    upsert_all(session, Play, rows)
    session.commit()
    p2 = session.get(Play, ("2026_P01_A_B", 2))
    assert p2.passer_id == "espn:1" and p2.yards_gained == 12.0
