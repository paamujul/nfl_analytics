"""Drives: where they start, what they produce, and how a game plan opens.

Three questions share one expensive scan here.

*Field position.* "Points per drive" is mostly a statement about where drives
begin -- a team starting on its own 20 and a team starting at midfield are not
running the same offense. Bucketing by start zone is what separates the two.

*Script.* "How they start" is the most-asked coaching question and it is not a
drive statistic at all: a scripted opening is a property of the first ~15
offensive calls of the *game*, which may span four drives or one. So the script
split is computed over game-ordered offensive plays, not drive-ordered ones.
Fifteen is the conventional number (Walsh's opening script) and is a parameter
rather than a constant because nobody outside a building knows the real length.

*Sequencing.* Whether the previous drive's result shifts the next drive's
opening call. This is the closest thing in public data to watching a
coordinator adjust, and every cell carries its own count because the cells get
thin fast -- a team has ~11 drives a game and only some follow a turnover.

Drive identity is (game_id, posteam, fixed_drive). `fixed_drive` is nflverse's
repaired drive counter and is preferred over `drive`, which is the raw feed's
and skips numbers. `drive_start_yard_line` is prose ("BAL 30", or a bare "50"),
so it is parsed here rather than trusted as a number.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from sqlalchemy import Integer, Row, case, func, select
from sqlalchemy.orm import Session

from app.analytics.personnel import offense_grouping, rate_table
from app.cache import league_cache
from app.db.models import Game, Play, Team

SCRIPT_LENGTH = 15

# Points credited to the offense for each fixed_drive_result. "Opp touchdown"
# is a pick-six or scoop-and-score charged against the drive that produced it.
# Touchdowns are worth 7 rather than 6.94: this is a coaching-facing summary,
# and modelling the extra point would obscure more than it adds.
DRIVE_POINTS = {
    "Touchdown": 7.0,
    "Field goal": 3.0,
    "Opp touchdown": -7.0,
    "Safety": -2.0,
}

_PUNT = "Punt"

# Plays that count against a drive's play count. Penalties (play_type
# "no_play") deliberately do not: a drive extended by a defensive hold and then
# punted on the next snap is still a three-and-out in coaching language.
SCRIMMAGE_TYPES = ("pass", "run", "qb_kneel", "qb_spike")

_YARDLINE = re.compile(r"^\s*(?:([A-Z]{2,4})\s+)?(\d{1,2})\s*$")


def start_yards_from_own_goal(text: str | None, posteam: str | None) -> int | None:
    """"BAL 30" -> 30 for BAL, 70 for its opponent. A bare "50" -> 50.

    Returns yards from the offense's own goal line, so bigger is better field
    position. None for anything unparseable, rather than a guess.
    """
    if not text:
        return None
    m = _YARDLINE.match(text)
    if not m:
        return None
    side, num = m.group(1), int(m.group(2))
    if not 0 <= num <= 50:
        return None
    if side is None or num == 50:
        return 50
    return num if (posteam and side == posteam) else 100 - num


def start_zone(yards: int | None) -> str | None:
    if yards is None:
        return None
    if yards <= 20:
        return "own_1_20"
    if yards <= 40:
        return "own_21_40"
    if yards <= 60:
        return "midfield"
    return "opp_territory"


ZONE_ORDER = ["own_1_20", "own_21_40", "midfield", "opp_territory"]


def _drive_rows(session: Session, season: int, phase: str) -> list[Row]:
    """One row per (game, team, drive), aggregated in SQL.

    Pushed into a GROUP BY rather than reduced in Python: a season is ~50k
    plays and this collapses them to ~6k drives before anything crosses the
    wire. `start` and `result` are constant within a drive, so max() is simply
    "the value" -- and max() rather than min() because min over a nullable
    column would return NULL whenever the first play of a drive is uncharted.
    """
    scrim = case((Play.play_type.in_(SCRIMMAGE_TYPES), 1), else_=0)
    return session.execute(
        select(
            Play.game_id, Play.posteam, Play.fixed_drive,
            func.max(Play.drive_start_yard_line).label("start"),
            func.max(Play.fixed_drive_result).label("result"),
            func.sum(Play.epa).label("epa"),
            func.sum(func.cast(scrim, Integer)).label("scrim"),
        )
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase,
               Play.posteam.isnot(None), Play.fixed_drive.isnot(None))
        .group_by(Play.game_id, Play.posteam, Play.fixed_drive)
    ).all()


def _blank_drive_acc() -> dict:
    return {
        "drives": 0,
        "zone_counts": Counter(),
        "zone_epa": defaultdict(float),
        "zone_points": defaultdict(float),
        "zone_scored": Counter(),
        "start_sum": 0, "start_n": 0,
        "punts": 0, "three_and_outs": 0,
        "results": Counter(),
        "epa_sum": 0.0, "points_sum": 0.0,
    }


def _compute_league_drives(session: Session, season: int, phase: str) -> dict[str, dict]:
    by_team: dict[str, dict] = {}
    for d in _drive_rows(session, season, phase):
        acc = by_team.setdefault(d.posteam, _blank_drive_acc())
        acc["drives"] += 1
        acc["results"][d.result or "unknown"] += 1
        points = DRIVE_POINTS.get(d.result or "", 0.0)
        acc["points_sum"] += points
        acc["epa_sum"] += d.epa or 0.0

        yards = start_yards_from_own_goal(d.start, d.posteam)
        if yards is not None:
            acc["start_sum"] += yards
            acc["start_n"] += 1
        zone = start_zone(yards)
        if zone:
            acc["zone_counts"][zone] += 1
            acc["zone_epa"][zone] += d.epa or 0.0
            acc["zone_points"][zone] += points
            if points > 0:
                acc["zone_scored"][zone] += 1

        if d.result == _PUNT:
            acc["punts"] += 1
            # Three-and-out: punted having run three or fewer scrimmage plays.
            # The denominator is all drives, not all punts -- a team that never
            # punts has a 0% three-and-out rate, and that is the truth.
            if (d.scrim or 0) <= 3:
                acc["three_and_outs"] += 1

    return {team: _finalize_drives(acc) for team, acc in by_team.items()}


def _finalize_drives(acc: dict) -> dict:
    """Accumulator -> plain scalars. Nothing below may hold a Row or a Session."""
    n = acc["drives"]

    def rate(x, d=n, digits=3):
        return round(x / d, digits) if d else None

    zones = [{
        "zone": zone,
        "drives": acc["zone_counts"].get(zone, 0),
        "share": rate(acc["zone_counts"].get(zone, 0)),
        "epa_per_drive": rate(acc["zone_epa"].get(zone, 0.0),
                              acc["zone_counts"].get(zone, 0)),
        "points_per_drive": rate(acc["zone_points"].get(zone, 0.0),
                                 acc["zone_counts"].get(zone, 0), 2),
        "score_rate": rate(acc["zone_scored"].get(zone, 0),
                           acc["zone_counts"].get(zone, 0)),
    } for zone in ZONE_ORDER]

    return {
        "drives": n,
        "avg_start_yardline": rate(acc["start_sum"], acc["start_n"], 1),
        "epa_per_drive": rate(acc["epa_sum"], n),
        "points_per_drive": rate(acc["points_sum"], n, 2),
        "three_and_out_rate": rate(acc["three_and_outs"]),
        "punt_rate": rate(acc["punts"]),
        "by_start_zone": zones,
        "results": [{"result": k, "n": v, "rate": rate(v)}
                    for k, v in acc["results"].most_common()],
    }


def _league_drives(session: Session, season: int, phase: str) -> dict[str, dict]:
    """Cached league-wide reduction. Plain scalars only -- see app/cache.py.

    The Rows from _drive_rows never reach this value; _finalize_drives returns
    numbers, which is what makes it safe for the value to outlive the Session
    that built it.
    """
    return league_cache.get_or_set(
        ("drives", season, phase),
        lambda: _compute_league_drives(session, season, phase),
    )


# --- script and sequencing ------------------------------------------------
# These need play-level ordering inside a game, which the drive-level GROUP BY
# above has thrown away. One team is ~1000 rows; the league would be ~34k, so
# unlike the reduction above this is computed per request and not cached.

def _blank_call_acc() -> dict:
    return {"n": 0, "pass": 0, "shotgun_hits": 0, "shotgun_n": 0,
            "pa_hits": 0, "pa_n": 0, "epa": 0.0, "epa_n": 0,
            "success": 0, "success_n": 0, "groupings": Counter()}


def _add_call(b: dict, p: Row) -> None:
    b["n"] += 1
    b["pass"] += 1 if p.play_type == "pass" else 0
    if p.shotgun is not None:
        b["shotgun_n"] += 1
        b["shotgun_hits"] += 1 if p.shotgun else 0
    if p.is_play_action is not None:
        b["pa_n"] += 1
        b["pa_hits"] += 1 if p.is_play_action else 0
    if p.epa is not None:
        b["epa"] += p.epa
        b["epa_n"] += 1
    if p.success is not None:
        b["success_n"] += 1
        b["success"] += 1 if p.success else 0
    grouping = offense_grouping(p.offense_personnel)
    if grouping:
        b["groupings"][grouping] += 1


def _finalize_calls(b: dict) -> dict:
    def r(x, d, digits=3):
        return round(x / d, digits) if d else None
    return {
        "plays": b["n"],
        "pass_rate": r(b["pass"], b["n"]),
        # Sparse-column shape even for shotgun, which is nearly complete: the
        # caller should not have to know which columns happen to be dense.
        "shotgun_rate": {"value": r(b["shotgun_hits"], b["shotgun_n"]),
                         "n": b["shotgun_n"], "coverage": r(b["shotgun_n"], b["n"])},
        "play_action_rate": {"value": r(b["pa_hits"], b["pa_n"]),
                             "n": b["pa_n"], "coverage": r(b["pa_n"], b["n"])},
        "epa_per_play": r(b["epa"], b["epa_n"]),
        "success_rate": r(b["success"], b["success_n"]),
        "personnel_mix": rate_table(b["groupings"])[:5],
    }


def _script_and_sequence(session: Session, team: str, season: int, phase: str,
                         script_length: int) -> dict:
    plays = session.execute(
        select(Play.game_id, Play.play_id, Play.fixed_drive, Play.play_type,
               Play.shotgun, Play.epa, Play.success, Play.offense_personnel,
               Play.is_play_action)
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase, Play.posteam == team,
               Play.play_type.in_(("pass", "run")))
        .order_by(Play.game_id, Play.play_id)
    ).all()

    results: dict[tuple[str, int], str | None] = {
        (gid, fd): res
        for gid, fd, res in session.execute(
            select(Play.game_id, Play.fixed_drive, func.max(Play.fixed_drive_result))
            .join(Game, Game.id == Play.game_id)
            .where(Game.season == season, Game.phase == phase,
                   Play.posteam == team, Play.fixed_drive.isnot(None))
            .group_by(Play.game_id, Play.fixed_drive)
        ).all()
    }

    scripted, rest = _blank_call_acc(), _blank_call_acc()
    seen_in_game: Counter[str] = Counter()

    # Sequencing state, walked in the same single ordered pass.
    current_drive: dict[str, int | None] = {}
    prev_result: dict[str, str | None] = {}   # result of the drive before this one
    last_finished: dict[str, str | None] = {}  # result of the drive we just left
    openers: dict[str, dict] = defaultdict(_blank_call_acc)
    whole: dict[str, dict] = defaultdict(_blank_call_acc)

    for p in plays:
        gid = p.game_id
        seen_in_game[gid] += 1
        _add_call(scripted if seen_in_game[gid] <= script_length else rest, p)

        is_new_drive = current_drive.get(gid) != p.fixed_drive
        if is_new_drive:
            current_drive[gid] = p.fixed_drive
            prev_result[gid] = last_finished.get(gid)
            last_finished[gid] = results.get((gid, p.fixed_drive))

        key = prev_result.get(gid)
        if key:
            if is_new_drive:
                _add_call(openers[key], p)
            _add_call(whole[key], p)

    return {
        "script_length": script_length,
        "scripted": _finalize_calls(scripted),
        "rest_of_game": _finalize_calls(rest),
        # Sorted by sample size: the thin cells (safeties, opp touchdowns) are
        # the ones a reader is most likely to over-read, so they sort last.
        "after_previous_drive": [
            {"previous_result": k,
             "opening_play": _finalize_calls(openers[k]),
             "whole_drive": _finalize_calls(whole[k])}
            for k in sorted(whole, key=lambda k: -whole[k]["n"])
        ],
    }


def team_drives(session: Session, team: str, season: int, phase: str,
                script_length: int = SCRIPT_LENGTH) -> dict:
    league = _league_drives(session, season, phase)
    mine = league.get(team) or _finalize_drives(_blank_drive_acc())

    def league_avg(key):
        vals = [m[key] for m in league.values() if m.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    t = session.get(Team, team)
    return {
        "team": {"abbr": team, "name": t.name if t else team,
                 "color": t.color if t else None, "logo": t.logo if t else None},
        "season": season, "phase": phase,
        "drives": mine,
        "league_average": {k: league_avg(k) for k in
                           ("avg_start_yardline", "epa_per_drive",
                            "points_per_drive", "three_and_out_rate")},
        "script": _script_and_sequence(session, team, season, phase, script_length),
    }
