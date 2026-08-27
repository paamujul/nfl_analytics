"""ESPN normalizers vs a recorded 2026 preseason fixture (LV @ HOU, event 401873286)."""
import json
from pathlib import Path

import pytest

from app.data import espn_source as es

FIXTURE = Path(__file__).parent / "fixtures" / "espn_summary_401873286.json"


@pytest.fixture(scope="module")
def summary():
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def game(summary):
    return es.normalize_summary_header(summary)


def test_header(game):
    assert game["id"] == "2026_P03_LV_HOU"
    assert game["season"] == 2026 and game["phase"] == "pre" and game["week"] == 3
    assert game["home_team"] == "HOU" and game["away_team"] == "LV"
    assert game["status"] == "final"


def test_boxscore_matches_espn_display(summary, game):
    players, stats = es.normalize_boxscore(summary, game["id"])
    by_name = {p["name"]: p["id"] for p in players}
    oconnell = next(s for s in stats if s["player_id"] == by_name["Aidan O'Connell"])
    # ESPN's own box score showed 15/24, 166 yds, 0 TD, 0 INT
    assert oconnell["pass_cmp"] == 15 and oconnell["pass_att"] == 24
    assert oconnell["pass_yds"] == 166 and oconnell["pass_int"] == 0


def test_play_attribution_rate(summary, game):
    players, _ = es.normalize_boxscore(summary, game["id"])
    plays = es.normalize_plays(summary, game["id"], players,
                               game["home_team"], game["away_team"])
    scrim = [p for p in plays if p["play_type"] in ("pass", "run")]
    attributed = [p for p in scrim if p.get("passer_id") or p.get("rusher_id")]
    assert len(scrim) > 100
    assert len(attributed) / len(scrim) > 0.9
    # every play carries a quarter and most passes carry a direction
    assert all(p["quarter"] for p in scrim)
    passes = [p for p in scrim if p["play_type"] == "pass" and not p["sack"]]
    with_loc = [p for p in passes if p.get("pass_location")]
    assert len(with_loc) / len(passes) > 0.9


def test_pass_yards_roughly_reconcile(summary, game):
    """Parsed per-play passing yards should land near the official box score."""
    players, stats = es.normalize_boxscore(summary, game["id"])
    plays = es.normalize_plays(summary, game["id"], players,
                               game["home_team"], game["away_team"])
    by_name = {p["name"]: p["id"] for p in players}
    pid = by_name["Aidan O'Connell"]
    parsed = sum(p["yards_gained"] for p in plays
                 if p.get("passer_id") == pid and p.get("complete_pass"))
    official = next(s for s in stats if s["player_id"] == pid)["pass_yds"]
    assert abs(parsed - official) <= 20  # laterals/penalty quirks allowed


def test_name_key():
    assert es._norm_name_key("C.J. Stroud") == "c.stroud"
    assert es._norm_name_key("C.Stroud") == "c.stroud"
    assert es._norm_name_key("Mike Washington Jr.") == "m.washington"
    assert es._norm_name_key("Ja'Marr Chase") == "j.chase"


def test_game_id_convention():
    assert es.game_id_for(2026, "pre", 3, "LV", "HOU") == "2026_P03_LV_HOU"
    # regular season matches nflverse ids so rows merge across sources
    assert es.game_id_for(2026, "reg", 1, "KC", "LAC") == "2026_01_KC_LAC"
    assert es.game_id_for(2026, "post", 1, "KC", "BUF") == "2026_19_KC_BUF"
