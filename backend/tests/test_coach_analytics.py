"""Tests for the coaching analytics package.

Two different jobs here, kept apart on purpose.

The personnel-parser tests are pure and exact. Their expected values are not
invented: they are the shape of real nflverse strings from both eras of the
backfill, and the grouping rule they pin was validated against the 2024 regular
season, where it reproduces BAL 12=31.5%/11=28.4%/21=20.5%/22=14.0% (n=1039),
SF 11=49.3%/21=35.5% (n=1014) and KC 11=50.0%/12=35.5% (n=1069). That validation
cannot run in CI -- it needs the 250k-row backfill, which is gitignored -- so
what is pinned here is the rule that produced it, most importantly that a
fullback counts as a back. Drop the FB term and those three teams collapse into
11 personnel and nothing reconciles.

The analytics tests run against a small hand-built season and check contracts
rather than numbers: that sparse columns arrive with their denominator attached,
that an absent season of FTN charting reports absent rather than zero, and that
the cached league reductions hold nothing that dies with the Session.
"""
import pytest

from app.analytics.defense_personnel import team_defense_personnel
from app.analytics.drives import (
    start_yards_from_own_goal, start_zone, team_drives,
)
from app.analytics.personnel import (
    charted, defense_grouping, defense_package, offense_grouping,
    parse_personnel, rate_table,
)
from app.analytics.playbook import (
    FTN_FIRST_SEASON, play_caller_playbook, team_playbook,
)
from app.analytics.usage import team_usage
from app.cache import league_cache
from app.db.models import (
    Coach, CoachingStaff, DepthChartEntry, Game, Play, Player,
    PlayerSeasonAdvanced, SnapCount, Team,
)
from app.db.upsert import upsert_all

# --- personnel parsing ----------------------------------------------------

# The 2023+ full roll-call. Every one of these is a verbatim shape from the
# backfill, not a constructed example.
FULL_11 = "1 C, 1 G, 1 QB, 1 RB, 3 T, 1 TE, 3 WR"
FULL_12 = "1 C, 1 G, 1 QB, 1 RB, 3 T, 2 TE, 2 WR"
FULL_21 = "1 C, 1 FB, 1 G, 1 QB, 1 RB, 3 T, 1 TE, 2 WR"
FULL_22 = "1 C, 1 FB, 1 G, 1 QB, 1 RB, 3 T, 2 TE, 1 WR"
# The 2021-22 short form, which lists skill positions only and never emits FB.
SHORT_11 = "1 RB, 1 TE, 3 WR"
SHORT_21 = "2 RB, 1 TE, 2 WR"


def test_grouping_counts_fullback_as_a_back():
    """The single most important line in the package.

    nflverse lists FB separately from RB in the 2023+ strings. Ignoring it
    turns 21 personnel into 11 and 22 into 12, which is precisely the two
    groupings that make Shanahan's and Harbaugh's offenses distinctive.
    """
    assert offense_grouping(FULL_21) == "21"
    assert offense_grouping(FULL_22) == "22"


def test_grouping_ignores_offensive_line():
    """The C/G/T/QB slots in the 2023+ form must not reach either digit."""
    assert offense_grouping(FULL_11) == "11"
    assert offense_grouping(FULL_12) == "12"


def test_grouping_is_stable_across_the_two_string_formats():
    """2021-22 and 2023+ must produce the same grouping for the same package.

    A team's five-season trend line runs straight through the format change,
    so an era-dependent parser would show a fake discontinuity in 2023.
    """
    assert offense_grouping(SHORT_11) == offense_grouping(FULL_11) == "11"
    # A fullback in the short form is simply charted as a second RB.
    assert offense_grouping(SHORT_21) == offense_grouping(FULL_21) == "21"


def test_grouping_keeps_the_leading_zero():
    """"02" is empty-backfield two-tight-end and is not the number two."""
    assert offense_grouping("0 RB, 2 TE, 3 WR") == "02"


@pytest.mark.parametrize("bad", [None, "", "   ", "no digits here", ",,,"])
def test_malformed_personnel_returns_none_not_an_exception(bad):
    """These strings come off a charting feed. One bad row must not take out
    a season-wide aggregate."""
    assert parse_personnel(bad) is None
    assert offense_grouping(bad) is None
    assert defense_grouping(bad) is None
    assert defense_package(bad) is None


def test_defense_grouping_folds_positions_into_three_units():
    nickel = "3 CB, 2 DE, 2 DT, 1 FS, 2 ILB, 1 SS"
    assert defense_grouping(nickel) == "4-2-5"
    # Baltimore's edge rushers are charted OLB, so the front reads 2-4.
    assert defense_grouping("3 CB, 2 DT, 2 FS, 2 ILB, 2 OLB") == "2-4-5"


def test_defense_package_names_five_defensive_backs_nickel():
    """Base is four DBs, nickel five, dime six.

    Counting from three would call Baltimore's most common 2024 look -- 2-4-5,
    on 64% of its snaps -- a dime, which is wrong by a whole defensive back.
    """
    assert defense_package("3 CB, 2 DE, 2 DT, 1 FS, 2 ILB, 1 SS") == "nickel"
    assert defense_package("2 CB, 2 DE, 2 DT, 1 FS, 3 ILB, 1 SS") == "base"
    assert defense_package("4 CB, 2 DE, 2 DT, 1 FS, 1 ILB, 1 SS") == "dime"


def test_charted_carries_its_denominator():
    """A rate over a partial sample must be inseparable from its sample."""
    out = charted(3, 10, 100)
    assert out == {"value": 0.3, "n": 10, "coverage": 0.1}


def test_charted_empty_sample_is_none_not_zero():
    """A team with nothing charted did not do the thing zero times."""
    out = charted(0, 0, 50)
    assert out["value"] is None
    assert out["n"] == 0


def test_rate_table_sorts_by_frequency():
    rows = rate_table({"11": 5, "12": 10, "21": 5})
    assert [r["key"] for r in rows] == ["12", "11", "21"]
    assert rows[0]["rate"] == 0.5


# --- drive helpers --------------------------------------------------------

def test_drive_start_parses_both_sides_of_the_field():
    """drive_start_yard_line is prose and is relative to a named team."""
    assert start_yards_from_own_goal("BAL 30", "BAL") == 30
    assert start_yards_from_own_goal("KC 30", "BAL") == 70
    # Midfield ships as a bare "50" on some rows and "TEAM 50" on others.
    assert start_yards_from_own_goal("50", "BAL") == 50
    assert start_yards_from_own_goal("BAL 50", "BAL") == 50


@pytest.mark.parametrize("bad", [None, "", "BAL", "somewhere", "BAL 87"])
def test_drive_start_refuses_to_guess(bad):
    assert start_yards_from_own_goal(bad, "BAL") is None
    assert start_zone(start_yards_from_own_goal(bad, "BAL")) is None


def test_start_zones_are_ordered_by_field_position():
    assert start_zone(10) == "own_1_20"
    assert start_zone(25) == "own_21_40"
    assert start_zone(50) == "midfield"
    assert start_zone(75) == "opp_territory"


# --- a small hand-built season -------------------------------------------

SEASON = 2024
OTHER_SEASON = 2021  # before FTN charting exists


def _seed(session, season=SEASON, weeks=(1, 2)):
    """Two teams, two games, hand-written plays with known aggregates.

    Small enough to reason about: KC throws on 3 of 5 scrimmage plays in each
    game and runs 11 personnel on all but one snap.
    """
    upsert_all(session, Team, [
        {"abbr": "KC", "name": "Kansas City Chiefs"},
        {"abbr": "BAL", "name": "Baltimore Ravens"},
    ])
    upsert_all(session, Player, [
        {"id": "p-wr", "name": "Wide Out", "position": "WR", "team": "KC"},
        {"id": "p-rb", "name": "Run Back", "position": "RB", "team": "KC"},
    ])
    upsert_all(session, Coach, [
        {"id": "andy-reid", "name": "Andy Reid"},
        {"id": "john-harbaugh", "name": "John Harbaugh"},
    ])
    upsert_all(session, CoachingStaff, [
        # KC names a play-caller explicitly; BAL leaves it NULL so the
        # head-coach fallback is exercised.
        {"season": season, "team": "KC", "head_coach_id": "andy-reid",
         "offensive_play_caller_id": "andy-reid", "verified": False},
        {"season": season, "team": "BAL", "head_coach_id": "john-harbaugh",
         "offensive_play_caller_id": None, "verified": False,
         "note": "coordinator unverified"},
    ])

    games, plays = [], []
    for week in weeks:
        gid = f"{season}_0{week}_BAL_KC"
        games.append({"id": gid, "season": season, "phase": "reg", "week": week,
                      "home_team": "KC", "away_team": "BAL", "status": "final",
                      "source": "nflverse"})
        for i in range(5):
            is_pass = i < 3
            plays.append({
                "game_id": gid, "play_id": 100 + i,
                "posteam": "KC", "defteam": "BAL",
                "play_type": "pass" if is_pass else "run",
                "down": (i % 4) + 1, "ydstogo": 10 if i < 2 else 2,
                "yardline_100": 75 - i * 10,
                "yards_gained": 5.0, "epa": 0.1, "success": True,
                "offense_personnel": FULL_21 if i == 4 else FULL_11,
                "offense_formation": "SHOTGUN" if is_pass else "UNDER CENTER",
                "shotgun": is_pass, "no_huddle": False,
                # xpass/pass_oe charted on only three of five plays, so the
                # coverage field has something real to report.
                "xpass": 0.6 if i < 3 else None,
                "pass_oe": 10.0 if i < 3 else None,
                "fixed_drive": 1 if i < 3 else 2,
                "fixed_drive_result": "Touchdown" if i < 3 else "Punt",
                "drive_start_yard_line": "KC 25" if i < 3 else "KC 10",
                "defense_personnel": "3 CB, 2 DE, 2 DT, 1 FS, 2 ILB, 1 SS",
                "defenders_in_box": 6,
                # was_pressure charted on two of the three pass plays.
                "was_pressure": (i == 0) if is_pass and i < 2 else None,
                "n_pass_rushers": 5 if is_pass else None,
                # man/zone charted on one play only -- a deliberately thin
                # sample, so a test can assert the denominator travels with it.
                "defense_man_zone_type": "ZONE_COVERAGE" if i == 0 else None,
                "defense_coverage_type": "COVER_3" if i == 0 else None,
                "receiver_id": "p-wr" if is_pass else None,
                "rusher_id": "p-rb" if not is_pass else None,
                "route": "SLANT" if is_pass else None,
                "is_play_action": is_pass if season >= FTN_FIRST_SEASON else None,
                "is_motion": False if season >= FTN_FIRST_SEASON else None,
                "is_screen_pass": False if season >= FTN_FIRST_SEASON else None,
                "is_rpo": False if season >= FTN_FIRST_SEASON else None,
            })
        upsert_all(session, SnapCount, [{
            "game_id": gid, "player_name": "Wide Out", "team": "KC",
            "season": season, "week": week, "position": "WR",
            "offense_snaps": 5, "offense_pct": 1.0,
        }])
    upsert_all(session, Game, games)
    upsert_all(session, Play, plays)
    upsert_all(session, DepthChartEntry, [{
        "season": season, "week": 1, "game_type": "REG", "team": "BAL",
        "formation": "Defense", "depth_position": "DE", "player_id": "p-de",
        "depth_team": 1, "position": "DE", "player_name": "Edge Guy",
    }])
    upsert_all(session, PlayerSeasonAdvanced, [{
        "season": season, "pfr_id": "EdgeG00", "player_id": "p-de",
        "player_name": "Edge Guy", "team": "BAL", "position": "DE",
        "games": 17, "prss": 40.0, "hrry": 20.0, "qbkd": 10.0,
        "comb": 50.0, "m_tkl_percent": 5.0,
    }])
    session.commit()


@pytest.fixture()
def seeded(session):
    league_cache.clear()
    _seed(session)
    yield session
    league_cache.clear()


# --- playbook -------------------------------------------------------------

def test_playbook_reports_the_grouping_mix(seeded):
    pb = team_playbook(seeded, "KC", SEASON, "reg")["playbook"]
    mix = {r["key"]: r["n"] for r in pb["personnel"]["mix"]}
    assert mix == {"11": 8, "21": 2}
    assert pb["personnel"]["coverage"] == 1.0
    assert pb["pass_rate"] == 0.6


def test_playbook_cross_tabs_grouping_by_situation(seeded):
    pb = team_playbook(seeded, "KC", SEASON, "reg")["playbook"]
    # The one 21-personnel snap per game is 2nd-and-2 (i == 4 -> down 1).
    assert "by_down" in pb["personnel"] and pb["personnel"]["by_down"]
    assert set(pb["personnel"]["by_distance"]) <= {"short", "medium", "long"}
    assert set(pb["personnel"]["by_field_zone"]) <= {
        "backed_up", "own_territory", "opp_territory", "red_zone"}


def test_playbook_ftn_is_absent_not_zero_before_2022(session):
    """The trap this package exists to avoid.

    FTN charting starts in 2022. Reporting 0% play-action for 2021 would be a
    confident, specific, wrong statement about a coordinator.
    """
    league_cache.clear()
    _seed(session, season=OTHER_SEASON)
    pb = team_playbook(session, "KC", OTHER_SEASON, "reg")["playbook"]
    for key in ("play_action", "motion", "screen", "rpo"):
        assert pb["ftn"][key]["value"] is None
        assert "2022" in pb["ftn"][key]["reason"]
    league_cache.clear()


def test_playbook_ftn_carries_a_sample_from_2022(seeded):
    pa = team_playbook(seeded, "KC", SEASON, "reg")["playbook"]["ftn"]["play_action"]
    assert pa["value"] == 0.6           # the six pass plays of ten
    assert pa["n"] == 10 and pa["coverage"] == 1.0
    assert "reason" not in pa


def test_playbook_sparse_columns_carry_denominators(seeded):
    pb = team_playbook(seeded, "KC", SEASON, "reg")["playbook"]
    # xpass is charted on 6 of 10 scrimmage plays in the fixture.
    assert pb["xpass"]["n"] == 6
    assert pb["pass_oe"]["n"] == 6
    assert pb["pass_oe"]["value"] == 10.0


def test_playbook_surfaces_the_unverified_flag(seeded):
    """Coordinator attribution is model-generated. It must never be presented
    as fact, so `verified` travels with the name."""
    caller = team_playbook(seeded, "KC", SEASON, "reg")["play_caller"]
    assert caller["name"] == "Andy Reid"
    assert caller["verified"] is False


def test_playbook_falls_back_to_head_coach_and_says_so(seeded):
    caller = team_playbook(seeded, "BAL", SEASON, "reg")["play_caller"]
    assert caller["name"] == "John Harbaugh"
    assert caller["fell_back_to_head_coach"] is True
    assert caller["is_head_coach"] is True
    assert caller["note"] == "coordinator unverified"


def test_play_caller_view_aggregates_and_flags_verification(seeded):
    out = play_caller_playbook(seeded, "andy-reid", SEASON, "reg")
    assert out["playbook"]["plays"] == 10
    assert out["verified"] is False
    assert [t["team"] for t in out["teams"]] == ["KC"]


def test_play_caller_view_rejects_an_unknown_coach(seeded):
    with pytest.raises(ValueError):
        play_caller_playbook(seeded, "nobody", SEASON, "reg")


def test_playbook_handles_a_team_with_no_plays(seeded):
    """An empty result must be shaped like a full one, not a KeyError."""
    pb = team_playbook(seeded, "DET", SEASON, "reg")["playbook"]
    assert pb["plays"] == 0
    assert pb["pass_rate"] is None


# --- drives ---------------------------------------------------------------

def test_drives_bucket_by_start_zone(seeded):
    out = team_drives(seeded, "KC", SEASON, "reg")["drives"]
    assert out["drives"] == 4  # two drives per game, two games
    zones = {z["zone"]: z["drives"] for z in out["by_start_zone"]}
    assert zones["own_21_40"] == 2   # the KC 25 drives
    assert zones["own_1_20"] == 2    # the KC 10 drives


def test_drives_score_and_three_and_out(seeded):
    out = team_drives(seeded, "KC", SEASON, "reg")["drives"]
    # Two touchdown drives and two two-play punts.
    assert out["points_per_drive"] == 3.5
    assert out["three_and_out_rate"] == 0.5


def test_drive_zones_are_always_present_even_when_empty(seeded):
    """The frontend renders four zones. A missing key is a render bug."""
    out = team_drives(seeded, "KC", SEASON, "reg")["drives"]
    assert [z["zone"] for z in out["by_start_zone"]] == [
        "own_1_20", "own_21_40", "midfield", "opp_territory"]
    assert out["by_start_zone"][2]["points_per_drive"] is None


def test_script_splits_the_opening_calls_from_the_rest(seeded):
    script = team_drives(seeded, "KC", SEASON, "reg", script_length=3)["script"]
    # Three scripted plays per game, two games.
    assert script["scripted"]["plays"] == 6
    assert script["rest_of_game"]["plays"] == 4
    assert script["scripted"]["pass_rate"] == 1.0
    assert script["rest_of_game"]["pass_rate"] == 0.0


def test_sequencing_conditions_on_the_previous_drive(seeded):
    script = team_drives(seeded, "KC", SEASON, "reg")["script"]
    seq = {r["previous_result"]: r for r in script["after_previous_drive"]}
    # Only the second drive of each game has a predecessor, and it was a TD.
    assert set(seq) == {"Touchdown"}
    assert seq["Touchdown"]["opening_play"]["plays"] == 2
    assert seq["Touchdown"]["whole_drive"]["plays"] == 4


# --- usage ----------------------------------------------------------------

def test_usage_shares_use_the_right_denominator(seeded):
    out = team_usage(seeded, "KC", SEASON, "reg")
    by_id = {p["player_id"]: p for p in out["players"]}
    assert by_id["p-wr"]["target_share"] == 1.0   # 6 targets / 6 pass plays
    assert by_id["p-rb"]["carry_share"] == 1.0    # 4 carries / 4 run plays
    # Targets are not divided by total plays; a receiver on every pass is 100%.
    assert by_id["p-wr"]["carry_share"] == 0.0


def test_usage_exposes_a_weekly_trajectory(seeded):
    out = team_usage(seeded, "KC", SEASON, "reg")
    traj = {p["player_id"]: p for p in out["players"]}["p-wr"]["target_trajectory"]
    assert [w["week"] for w in traj["weeks"]] == [1, 2]
    assert traj["trend"] == 0.0  # flat across two identical weeks


def test_usage_single_week_reports_no_trend(session):
    """One game is not a trajectory. A 0.0 trend there reads as 'stable'."""
    league_cache.clear()
    # Seeded with one week rather than seeded-then-deleted: plays carry an FK
    # onto games, which SQLite ignores and Postgres enforces.
    _seed(session, weeks=(1,))
    out = team_usage(session, "KC", SEASON, "reg")
    traj = {p["player_id"]: p for p in out["players"]}["p-wr"]["target_trajectory"]
    assert traj["trend"] is None
    league_cache.clear()


def test_usage_route_mix_is_not_a_route_share(seeded):
    """Play.route is the *targeted* receiver's route -- one per play, not one
    per eligible receiver -- so it can only support a mix, and the mix carries
    its own count."""
    out = team_usage(seeded, "KC", SEASON, "reg")
    mix = {p["player_id"]: p for p in out["players"]}["p-wr"]["route_mix"]
    assert mix["n"] == 6
    assert mix["mix"][0]["key"] == "SLANT"
    assert mix["coverage"] == 1.0


def test_usage_names_a_lead_back_only_above_the_threshold(seeded):
    out = team_usage(seeded, "KC", SEASON, "reg")
    assert out["lead_back"]["player_id"] == "p-rb"
    assert out["backfield_is_committee"] is False


def test_usage_snap_counts_absent_outside_the_regular_season(seeded):
    """PFR does not publish preseason snap counts. Say so; do not report 0%."""
    out = team_usage(seeded, "KC", SEASON, "pre")
    assert out["snap_counts_available"] is False


# --- defensive personnel --------------------------------------------------

def test_defense_reports_package_and_grouping(seeded):
    d = team_defense_personnel(seeded, "BAL", SEASON, "reg")["defense"]
    assert d["personnel"]["mix"][0]["key"] == "4-2-5"
    assert d["personnel"]["packages"][0]["key"] == "nickel"


def test_defense_pressure_denominator_is_pass_plays(seeded):
    """Pressure is a pass-play statistic. Measuring its coverage against all
    scrimmage plays would make a complete column look 60% filled."""
    d = team_defense_personnel(seeded, "BAL", SEASON, "reg")["defense"]
    assert d["pressure_rate"]["n"] == 4     # charted on 2 of 3 passes per game
    assert d["pressure_rate"]["coverage"] == round(4 / 6, 3)
    assert d["pressure_rate"]["value"] == 0.5


def test_defense_man_zone_carries_a_thin_sample_honestly(seeded):
    """Two charted plays out of ten. The rate is 100% zone and that is a fact
    about the sample, not about the defense -- so n and coverage must be there
    for the caller to see."""
    d = team_defense_personnel(seeded, "BAL", SEASON, "reg")["defense"]
    assert d["zone_rate"]["value"] == 1.0
    assert d["zone_rate"]["n"] == 2
    assert d["zone_rate"]["coverage"] == 0.2
    assert d["man_rate"]["value"] == 0.0


def test_defense_player_stats_come_from_pfr_not_was_pressure(seeded):
    """was_pressure says the QB was pressured, not who did it. Per-player
    attribution has exactly one valid source."""
    out = team_defense_personnel(seeded, "BAL", SEASON, "reg")
    assert "pro-football-reference" in out["players"]["source"]
    top = out["players"]["top_pass_rushers"][0]
    assert top["name"] == "Edge Guy" and top["pressures"] == 40.0


def test_defense_starters_come_from_the_depth_chart(seeded):
    out = team_defense_personnel(seeded, "BAL", SEASON, "reg")
    assert [p["name"] for p in out["starters"]["DL"]] == ["Edge Guy"]


# --- cache safety ---------------------------------------------------------

@pytest.mark.parametrize("fn", [team_playbook, team_drives, team_defense_personnel])
def test_league_reductions_survive_a_closed_session(seeded, engine, fn):
    """The cached value outlives the request that built it.

    app/cache.py holds it for 300s while get_db closes the Session on the way
    out of the request, so an ORM instance or a Row in the cached dict would be
    a DetachedInstanceError on the very next hit. Closing the session and
    reading again is the cheapest way to catch that.
    """
    from sqlalchemy.orm import sessionmaker

    fn(seeded, "KC", SEASON, "reg")
    seeded.close()

    second = sessionmaker(bind=engine)()
    try:
        out = fn(second, "KC", SEASON, "reg")
        # forces the whole structure through a JSON-shaped walk
        import json
        json.dumps(out)
    finally:
        second.close()


# --- routes ---------------------------------------------------------------

@pytest.fixture()
def client(engine, seeded):
    """TestClient over the seeded season.

    Plain TestClient, not a context manager, so the app lifespan -- and with it
    the self-seed and the live ingester -- never starts. Mirrors tests/test_api.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.db.session import get_db
    from app.main import app

    Testing = sessionmaker(bind=engine)

    def override_get_db():
        s = Testing()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


COACH_ROUTES = ["playbook", "drives", "usage", "defense"]


@pytest.mark.parametrize("name", COACH_ROUTES)
def test_coach_routes_execute_on_both_backends(client, name):
    """Dialect coverage, not analytics correctness -- the same reason
    tests/test_api.py exists. A SQLite-only construct in one of these would
    otherwise surface for the first time in production."""
    r = client.get(f"/api/coach/{name}",
                   params={"team": "KC", "season": SEASON, "phase": "reg"})
    assert r.status_code == 200, r.text
    assert r.json()["season"] == SEASON


@pytest.mark.parametrize("name", COACH_ROUTES)
def test_coach_routes_are_edge_cacheable(client, name):
    """These sit behind the same 300s league cache as /api/compare, so the
    browser TTL has to agree with it."""
    r = client.get(f"/api/coach/{name}",
                   params={"team": "KC", "season": SEASON, "phase": "reg"})
    assert r.headers["Cache-Control"] == "public, max-age=300"


def test_playbook_route_accepts_a_coach_instead_of_a_team(client):
    r = client.get("/api/coach/playbook",
                   params={"coach": "andy-reid", "season": SEASON, "phase": "reg"})
    assert r.status_code == 200
    assert r.json()["coach"]["name"] == "Andy Reid"


def test_playbook_route_rejects_both_or_neither(client):
    for params in ({}, {"team": "KC", "coach": "andy-reid"}):
        r = client.get("/api/coach/playbook",
                       params={**params, "season": SEASON, "phase": "reg"})
        assert r.status_code == 400


def test_playbook_route_404s_on_an_unknown_coach(client):
    r = client.get("/api/coach/playbook",
                   params={"coach": "nobody", "season": SEASON, "phase": "reg"})
    assert r.status_code == 404


def test_drives_route_rejects_an_absurd_script_length(client):
    r = client.get("/api/coach/drives", params={
        "team": "KC", "season": SEASON, "phase": "reg", "script_length": 500})
    assert r.status_code == 400
