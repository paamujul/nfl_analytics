"""Validation against the ingested 2025 season (the 'test on 2025 first' pass).

These run against the real SQLite store and are skipped when it hasn't been
backfilled yet (fresh clone) — run `python -m app.cli backfill-nflverse 2025`.
"""
import pytest
from sqlalchemy import func, select

from app.analytics.lineup_impact import lineup_impact
from app.analytics.player_quarters import player_quarter_splits
from app.analytics.team_stats import season_team_totals
from app.config import DB_PATH
from app.db.models import Game, Play, PlayerGameStat
from app.db.session import SessionLocal

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="database not backfilled")


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    has_2025 = s.scalar(select(func.count(Game.id)).where(Game.season == 2025))
    if not has_2025:
        s.close()
        pytest.skip("2025 season not backfilled")
    yield s
    s.close()


def test_full_2025_regular_season_present(db):
    totals = season_team_totals(db, 2025, "reg")
    assert len([t for t in totals if t["games"] > 0]) == 32
    for t in totals:
        if not t["games"]:
            continue
        assert t["games"] == 17
        # team passing yards must equal team receiving yards
        assert t["pass_yds"] == t["rec_yds"], t["abbr"]
        # plausible NFL season ranges
        assert 2300 <= t["pass_yds"] <= 5800, (t["abbr"], t["pass_yds"])
        assert 1000 <= t["rush_yds"] <= 3600, (t["abbr"], t["rush_yds"])


def test_pbp_agrees_with_official_box_scores(db):
    """Quarter splits come from pbp; official stats from the stats feed.
    The two independent sources must agree closely at the team-season level."""
    pbp_pass = dict(db.execute(
        select(Play.posteam, func.sum(Play.yards_gained))
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == 2025, Game.phase == "reg",
               Play.play_type == "pass", Play.complete_pass.is_(True))
        .group_by(Play.posteam)
    ).all())
    official = {t["abbr"]: t["pass_yds"] for t in season_team_totals(db, 2025, "reg")}
    for team, yds in official.items():
        if not yds:
            continue
        assert abs(pbp_pass.get(team, 0) - yds) / yds < 0.03, team


def test_quarter_splits_sum_to_player_totals(db):
    # top receiver by yards in 2025
    top = db.execute(
        select(PlayerGameStat.player_id, func.sum(PlayerGameStat.rec_yds).label("y"))
        .join(Game, Game.id == PlayerGameStat.game_id)
        .where(Game.season == 2025, Game.phase == "reg")
        .group_by(PlayerGameStat.player_id).order_by(func.sum(PlayerGameStat.rec_yds).desc())
    ).first()
    splits = player_quarter_splits(db, top.player_id, 2025, "reg")
    total_from_quarters = sum(q["rec_yds"] for q in splits["quarters"])
    assert abs(total_from_quarters - top.y) / top.y < 0.03
    assert splits["best_quarter"] in (1, 2, 3, 4, 5)
    assert len(splits["games"]) >= 10


def test_lineup_impact_play_level_with_guard(db):
    # take two high-snap defenders from one team's participation data
    play = db.scalars(
        select(Play).join(Game, Game.id == Play.game_id)
        .where(Game.season == 2025, Play.defense_players.isnot(None),
               Play.defteam == "KC", Play.play_type == "pass")
    ).first()
    two = play.defense_players.split(";")[:2]
    out = lineup_impact(db, "KC", "defense", two, 2025, "reg")
    assert out["method"] == "play_level"
    assert out["on"]["plays"] + out["off"]["plays"] > 500
    assert out["verdict"]
    # a fabricated player never appears -> tiny/zero sample must trip the guard
    out2 = lineup_impact(db, "KC", "defense", ["00-notreal"], 2025, "reg")
    assert not out2["sufficient"]
