"""Tests for the coaching data layer.

Deliberately offline: nothing here downloads from nflverse, so the suite stays
fast and works without network. The loaders' behaviour against real files was
verified separately against a single season; what is worth pinning in CI is the
shape contract -- the new columns exist and accept NULL, the coaches.yml seed
parses and stays internally consistent, and the coercion helpers do not turn
"not charted" into a charted zero.
"""
import yaml

from app.config import COACHES_YML
from app.data.nflverse_source import _bool, _int, _str
from app.db.models import (
    Coach, CoachingStaff, DepthChartEntry, Game, Play, Player, PlayerGameStat,
    PlayerSeasonAdvanced,
)
from app.db.upsert import upsert_all

TEAMS = 32
SEASONS = (2021, 2022, 2023, 2024, 2025)


def _game(session, game_id, season=2023):
    """plays and player_game_stats carry an FK onto games, which SQLite ignores
    and Postgres enforces -- the parent row has to exist."""
    upsert_all(session, Game, [{
        "id": game_id, "season": season, "phase": "reg", "week": 1,
        "home_team": "KC", "away_team": "DET", "status": "final",
        "source": "nflverse",
    }])
    session.commit()
    return game_id


# --- coercion helpers ---------------------------------------------------

def test_bool_preserves_null():
    """None must survive as None. Collapsing it to False would invent a charted
    'no' on plays that were never charted at all."""
    assert _bool(None) is None
    assert _bool(0.0) is False
    assert _bool(1.0) is True


def test_int_preserves_null_and_narrows_floats():
    assert _int(None) is None
    assert _int(1.0) == 1
    assert isinstance(_int(3.0), int)


def test_str_normalises_empty_to_null():
    """nflverse writes "" rather than null for an uncharted route/coverage.
    Storing "" would make IS NOT NULL report full coverage on a ~42% column."""
    assert _str("") is None
    assert _str("   ") is None
    assert _str(None) is None
    assert _str("ZONE_COVERAGE") == "ZONE_COVERAGE"


# --- coaches.yml seed ---------------------------------------------------

def _doc():
    return yaml.safe_load(COACHES_YML.read_text())


def test_coaches_yml_covers_32_teams_per_season():
    staff = _doc()["staff"]
    for season in SEASONS:
        assert season in staff, f"{season} missing from coaches.yml"
        assert len(staff[season]) == TEAMS, f"{season} has {len(staff[season])} teams"


def test_team_keys_are_strings_not_yaml_booleans():
    """Regression: a bare NO (New Orleans) is boolean false under YAML 1.1, so
    an unquoted key silently becomes False and later fails as the string
    'false' against team VARCHAR(4)."""
    staff = _doc()["staff"]
    for season in SEASONS:
        for team in staff[season]:
            assert isinstance(team, str), f"{season}: non-string team key {team!r}"
            assert len(team) <= 4
        assert "NO" in staff[season]


def test_every_referenced_slug_has_a_display_name():
    doc = _doc()
    names = doc["names"]
    roles = ("head_coach", "oc", "dc",
             "offensive_play_caller", "defensive_play_caller")
    for season in SEASONS:
        for team, entry in doc["staff"][season].items():
            for role in roles:
                slug = entry.get(role)
                if slug is not None:
                    assert slug in names, f"{season} {team} {role}: {slug!r} has no name"


def test_head_coach_is_always_present_and_coordinators_may_be_null():
    """head_coach is derived from load_schedules and must never be null.
    Coordinators are hand-entered and are null on purpose where unconfirmed --
    this asserts the file still contains such nulls, so nobody 'helpfully'
    backfills them with guesses without updating this test."""
    doc = _doc()
    nulls = 0
    for season in SEASONS:
        for team, entry in doc["staff"][season].items():
            assert entry["head_coach"], f"{season} {team} has no head coach"
            assert entry["verified"] is False
            nulls += sum(1 for r in ("oc", "dc", "offensive_play_caller",
                                     "defensive_play_caller") if entry.get(r) is None)
    assert nulls > 0, "coordinator nulls disappeared -- were they verified, or guessed?"


def test_mid_season_changes_carry_a_note():
    """Teams that changed head coach mid-season must say so, otherwise the
    recorded coach silently stands in for two people's play-calling."""
    doc = _doc()
    assert doc["staff"][2021]["JAX"]["note"], "JAX 2021 (Meyer -> Bevell) has no note"
    assert doc["staff"][2022]["IND"]["note"], "IND 2022 (Reich -> Saturday) has no note"


# --- schema shape -------------------------------------------------------

def test_play_accepts_null_for_every_new_column(session):
    """The whole coaching column set is nullable with no default. A play from a
    season with no FTN charting must still insert."""
    _game(session, "2021_01_AAA_BBB", season=2021)
    upsert_all(session, Play, [{"game_id": "2021_01_AAA_BBB", "play_id": 1}])
    session.commit()
    p = session.get(Play, ("2021_01_AAA_BBB", 1))
    for col in ("shotgun", "xpass", "pass_oe", "offense_formation",
                "offense_personnel", "defense_coverage_type", "route",
                "is_play_action", "is_rpo", "qb_location", "n_defense_box",
                "was_pressure", "time_to_throw"):
        assert getattr(p, col) is None, f"{col} should default to NULL"


def test_play_round_trips_a_fully_charted_row(session):
    _game(session, "2023_01_AAA_BBB")
    row = {
        "game_id": "2023_01_AAA_BBB", "play_id": 40,
        "shotgun": True, "no_huddle": False, "xpass": 0.61, "pass_oe": 33.9,
        "offense_formation": "SHOTGUN",
        # 2023+ lists all eleven; this is the widest form observed (66 chars)
        "offense_personnel": "1 C, 1 G, 1 QB, 1 RB, 3 T, 2 TE, 2 WR, 1 FB, 1 WR",
        "defense_man_zone_type": "ZONE_COVERAGE", "defense_coverage_type": "COVER_3",
        "route": "SHALLOW CROSS/DRAG", "is_play_action": True, "qb_location": "S",
        "fixed_drive_result": "Field goal", "series_result": "First down",
    }
    upsert_all(session, Play, [row])
    session.commit()
    p = session.get(Play, ("2023_01_AAA_BBB", 40))
    for k, v in row.items():
        assert getattr(p, k) == v, f"{k} did not round-trip"


def test_personnel_column_is_wide_enough(session):
    """Postgres raises on overlong strings and upsert_all clamps them silently,
    so an under-sized column would corrupt personnel rather than fail loudly."""
    widest = "1 C, 1 CB, 1 FB, 1 FS, 2 G, 1 QB, 1 RB, 2 T, 2 TE, 2 WR, 1 SS"
    _game(session, "g")
    upsert_all(session, Play, [{"game_id": "g", "play_id": 1,
                                "offense_personnel": widest,
                                "defense_personnel": widest}])
    session.commit()
    assert session.get(Play, ("g", 1)).offense_personnel == widest


def test_coaching_staff_play_caller_is_independent_of_coordinator(session):
    """The point of the play-caller columns: on many staffs the head coach calls
    the offense, so crediting the OC by default misattributes every call."""
    upsert_all(session, Coach, [
        {"id": "andy-reid", "name": "Andy Reid"},
        {"id": "matt-nagy", "name": "Matt Nagy"},
        {"id": "steve-spagnuolo", "name": "Steve Spagnuolo"},
    ])
    upsert_all(session, CoachingStaff, [{
        "season": 2023, "team": "KC", "head_coach_id": "andy-reid",
        "oc_id": "matt-nagy", "dc_id": "steve-spagnuolo",
        "offensive_play_caller_id": "andy-reid",
        "defensive_play_caller_id": "steve-spagnuolo",
        "verified": False, "note": None,
    }])
    session.commit()
    row = session.get(CoachingStaff, (2023, "KC"))
    assert row.oc_id == "matt-nagy"
    assert row.offensive_play_caller_id == "andy-reid"
    assert row.verified is False


def test_depth_chart_holds_both_upstream_shapes(session):
    """2021-2024 publish weekly charts; 2025 publishes dated snapshots with no
    week, which land as week=0. Both must coexist in one table."""
    upsert_all(session, DepthChartEntry, [
        {"season": 2023, "week": 1, "game_type": "REG", "team": "KC",
         "formation": "Defense", "depth_position": "CB",
         "player_id": "00-0036919", "depth_team": 1, "position": "CB",
         "player_name": "Trent McDuffie"},
        {"season": 2025, "week": 0, "game_type": "REG", "team": "ARI",
         "formation": "Defense", "depth_position": "LDE",
         "player_id": "00-0034768", "depth_team": 1, "position": "LDE",
         "player_name": "Josh Sweat"},
    ])
    session.commit()
    weekly = session.get(DepthChartEntry,
                         (2023, 1, "REG", "KC", "Defense", "CB", "00-0036919"))
    snap = session.get(DepthChartEntry,
                       (2025, 0, "REG", "ARI", "Defense", "LDE", "00-0034768"))
    assert weekly.depth_team == 1 and snap.depth_team == 1
    assert snap.week == 0


def test_player_season_advanced_keeps_pfr_id_and_resolved_gsis(session):
    """The table keys on pfr_id (what upstream gives) but carries the gsis id so
    it can actually join to `players`."""
    upsert_all(session, PlayerSeasonAdvanced, [{
        "season": 2023, "pfr_id": "WattT.00", "player_id": "00-0033886",
        "player_name": "TJ Watt", "team": "PIT", "prss": 50.0, "sk": 19.0,
    }])
    session.commit()
    row = session.get(PlayerSeasonAdvanced, (2023, "WattT.00"))
    assert row.player_id == "00-0033886"
    assert row.prss == 50.0


def test_defensive_box_score_columns_are_nullable(session):
    """ESPN's live ingester writes player_game_stats but has no defensive
    equivalent; NULL there means 'this source didn't say', not zero."""
    _game(session, "g1")
    upsert_all(session, Player, [{"id": "p1", "name": "Someone"}])
    upsert_all(session, PlayerGameStat, [
        {"game_id": "g1", "player_id": "p1", "team": "KC"}])
    session.commit()
    st = session.get(PlayerGameStat, ("g1", "p1"))
    assert st.def_sacks is None
    assert st.def_tackles_solo is None
    assert st.pass_att == 0  # offensive columns keep their 0 default


def test_half_sacks_survive(session):
    """def_sacks and def_tackles_for_loss are recorded in halves -- an integer
    column would round 2.5 sacks to 2."""
    _game(session, "g2")
    upsert_all(session, Player, [{"id": "p2", "name": "Someone Else"}])
    upsert_all(session, PlayerGameStat, [{
        "game_id": "g2", "player_id": "p2", "team": "KC",
        "def_sacks": 2.5, "def_tackles_for_loss": 1.5}])
    session.commit()
    st = session.get(PlayerGameStat, ("g2", "p2"))
    assert st.def_sacks == 2.5
    assert st.def_tackles_for_loss == 1.5


def test_seasons_config_spans_the_training_window():
    from app.config import SEASONS as CFG_SEASONS

    for s in SEASONS:
        assert s in CFG_SEASONS
