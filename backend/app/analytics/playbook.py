"""What a play-caller actually calls: personnel, formation, tempo, pass rate.

The question this answers is "what is this team's offensive identity", and the
honest version of that answer is a mix, not a number. Baltimore in 2024 ran 12
personnel on 31% of its scrimmage snaps and 11 on 28%; a single "pass rate"
summarises none of what makes that offense distinctive. So the unit of output
here is a distribution, and the distribution is repeated across the situations
where a coordinator's hand actually shows -- down, distance, field zone.

Three design points worth stating up front:

* Everything is keyed on the *team* first and attributed to a caller second.
  Play-calling attribution comes from `coaching_staff`, whose coordinator rows
  are model-generated and carry verified=False; see `_caller_for`. This module
  surfaces that flag on every caller it names rather than presenting the
  attribution as fact.

* The FTN charting columns (play-action, motion, screen, RPO) do not exist for
  2021 -- the feed starts in 2022. A 0% play-action rate for the 2021 Rams
  would be a lie about McVay specifically, so those fields come back as None
  with an explicit `reason` instead.

* `pass_oe` is nflverse's own pass-rate-over-expected. Run-heavy is negative:
  the 2024 floor is PHI -7.84 and the ceiling is CIN +8.94. It is stored per
  play, so the team figure is a mean -- but over a wider row set than every
  other field here, for the reason in `_accumulate_pass_oe`.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import Row, or_, select
from sqlalchemy.orm import Session

from app.analytics.personnel import charted, offense_grouping, rate_table
from app.cache import league_cache
from app.db.models import Coach, CoachingStaff, Game, Play, Team

# The first season FTN charts play-action / motion / screen / RPO. Before this
# the columns are absent, not false.
FTN_FIRST_SEASON = 2022
FTN_MISSING_REASON = (
    "FTN charting (play-action, motion, screen, RPO) begins in 2022; "
    "no data exists for this season"
)


def _distance_bin(ydstogo: int | None) -> str | None:
    if ydstogo is None:
        return None
    if ydstogo <= 3:
        return "short"
    if ydstogo <= 7:
        return "medium"
    return "long"


def _field_zone(yardline_100: int | None) -> str | None:
    """yardline_100 is yards to the opponent's goal line, so it counts down."""
    if yardline_100 is None:
        return None
    if yardline_100 > 80:
        return "backed_up"      # own 1-19
    if yardline_100 > 50:
        return "own_territory"  # own 20 to own 49
    if yardline_100 > 20:
        return "opp_territory"  # midfield to the opponent 21
    return "red_zone"


def _blank() -> dict:
    """The per-team accumulator. Counters only -- see _league_playbook."""
    return {
        "plays": 0,
        "games": set(),
        "groupings": Counter(),
        "grouping_by_down": defaultdict(Counter),
        "grouping_by_distance": defaultdict(Counter),
        "grouping_by_field_zone": defaultdict(Counter),
        "formations": Counter(),
        "personnel_charted": 0,
        "formation_charted": 0,
        "pass": 0,
        "run": 0,
        "shotgun_hits": 0, "shotgun_n": 0,
        "no_huddle_hits": 0, "no_huddle_n": 0,
        "xpass_sum": 0.0, "xpass_n": 0,
        "pass_oe_sum": 0.0, "pass_oe_n": 0, "pass_oe_rows": 0,
        "epa_sum": 0.0, "epa_n": 0,
        "ftn": {k: [0, 0] for k in ("play_action", "motion", "screen", "rpo")},
    }


_FTN_COLS = {
    "play_action": "is_play_action",
    "motion": "is_motion",
    "screen": "is_screen_pass",
    "rpo": "is_rpo",
}


def _accumulate_pass_oe(acc: dict, p: Row) -> None:
    """xpass / pass_oe only, over a wider row set than everything else.

    nflverse scores these on any *designed* pass or run, which includes plays
    wiped out by a penalty -- those land here as play_type "no_play" while
    still carrying a pass_oe. Published PROE tables (rbsdm and friends) filter
    on nflverse's `pass`/`rush` design flags rather than on play_type, so they
    include those rows, and excluding them moves a team by ~0.5: Baltimore's
    2024 figure is -6.82 over the published row set and -7.33 over play_type
    pass/run alone. Matching the published number matters more than internal
    tidiness here, because this is the one column in the module a reader can
    check against a public source.

    Note the asymmetry with `pass_rate` directly above it in the output, which
    deliberately uses only play_type pass/run: a play-call *mix* should not
    count a snap that was erased.
    """
    if p.xpass is not None:
        acc["xpass_sum"] += p.xpass
        acc["xpass_n"] += 1
    if p.pass_oe is not None:
        acc["pass_oe_sum"] += p.pass_oe
        acc["pass_oe_n"] += 1
        acc["pass_oe_rows"] += 1


def _accumulate(acc: dict, p: Row) -> None:
    acc["plays"] += 1
    acc["games"].add(p.game_id)
    if p.play_type == "pass":
        acc["pass"] += 1
    elif p.play_type == "run":
        acc["run"] += 1

    grouping = offense_grouping(p.offense_personnel)
    if grouping:
        acc["personnel_charted"] += 1
        acc["groupings"][grouping] += 1
        if p.down:
            acc["grouping_by_down"][str(int(p.down))][grouping] += 1
        dist = _distance_bin(p.ydstogo)
        if dist:
            acc["grouping_by_distance"][dist][grouping] += 1
        zone = _field_zone(p.yardline_100)
        if zone:
            acc["grouping_by_field_zone"][zone][grouping] += 1

    if p.offense_formation:
        acc["formation_charted"] += 1
        acc["formations"][p.offense_formation] += 1

    for field, col in (("shotgun", "shotgun"), ("no_huddle", "no_huddle")):
        v = getattr(p, col)
        if v is not None:
            acc[f"{field}_n"] += 1
            acc[f"{field}_hits"] += 1 if v else 0

    if p.epa is not None:
        acc["epa_sum"] += p.epa
        acc["epa_n"] += 1

    for key, col in _FTN_COLS.items():
        v = getattr(p, col)
        if v is not None:
            slot = acc["ftn"][key]
            slot[1] += 1
            slot[0] += 1 if v else 0


def _merge(a: dict, b: dict) -> dict:
    """Fold two accumulators. Used to build a caller's view across teams."""
    out = _blank()
    out["games"] = a["games"] | b["games"]
    for key in ("plays", "pass", "run", "personnel_charted", "formation_charted",
                "shotgun_hits", "shotgun_n", "no_huddle_hits", "no_huddle_n",
                "xpass_n", "pass_oe_n", "pass_oe_rows", "epa_n"):
        out[key] = a[key] + b[key]
    for key in ("xpass_sum", "pass_oe_sum", "epa_sum"):
        out[key] = a[key] + b[key]
    for key in ("groupings", "formations"):
        out[key] = a[key] + b[key]
    for key in ("grouping_by_down", "grouping_by_distance", "grouping_by_field_zone"):
        merged = defaultdict(Counter)
        for src in (a[key], b[key]):
            for bucket, counts in src.items():
                merged[bucket] += counts
        out[key] = merged
    for key, (hits, n) in a["ftn"].items():
        out["ftn"][key] = [hits + b["ftn"][key][0], n + b["ftn"][key][1]]
    return out


def _compute_league_playbook(session: Session, season: int, phase: str) -> dict[str, dict]:
    # Column-only select: see the note in analytics/compare.py. The plays table
    # is ~250k rows across the backfill and one season of scrimmage plays is
    # ~34k of them; hydrating Play entities here would pin every one of them in
    # the identity map for the life of the request.
    #
    # This reduction stays in Python rather than in SQL because the grouping
    # key does not exist in the database: offense_personnel is a positional
    # roll-call that has to be parsed and re-bucketed (see analytics/personnel),
    # and a GROUP BY on the raw string would produce ~200 distinct values per
    # team instead of ~8 groupings. Everything that *is* expressible as a plain
    # aggregate rides along in the same single pass.
    rows = session.execute(
        select(Play.game_id, Play.posteam, Play.play_type, Play.down, Play.ydstogo,
               Play.yardline_100, Play.offense_personnel, Play.offense_formation,
               Play.shotgun, Play.no_huddle, Play.xpass, Play.pass_oe, Play.epa,
               Play.is_play_action, Play.is_motion, Play.is_screen_pass, Play.is_rpo)
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase,
               or_(Play.play_type.in_(("pass", "run")),
                   Play.pass_oe.isnot(None)))
    ).all()

    by_team: dict[str, dict] = {}
    for p in rows:
        if not p.posteam:
            continue
        acc = by_team.setdefault(p.posteam, _blank())
        _accumulate_pass_oe(acc, p)
        if p.play_type in ("pass", "run"):
            _accumulate(acc, p)
    return {team: _finalize(acc, season) for team, acc in by_team.items()}


def _finalize(acc: dict, season: int) -> dict:
    """Accumulator -> plain scalars. Nothing below may hold a Row or a Session."""
    n = acc["plays"]
    scrim = acc["pass"] + acc["run"]

    def mean(total: float, count: int, digits: int = 3):
        return round(total / count, digits) if count else None

    ftn: dict = {}
    if season < FTN_FIRST_SEASON:
        for key in _FTN_COLS:
            ftn[key] = {"value": None, "n": 0, "coverage": None,
                        "reason": FTN_MISSING_REASON}
    else:
        for key, (hits, count) in acc["ftn"].items():
            ftn[key] = charted(hits, count, n)

    return {
        "plays": n,
        "games": len(acc["games"]),
        "pass_rate": mean(acc["pass"], scrim),
        "run_rate": mean(acc["run"], scrim),
        "epa_per_play": mean(acc["epa_sum"], acc["epa_n"]),
        # xpass / pass_oe are nflverse model outputs and are charted on nearly
        # every scrimmage play, but they still get the sparse shape: the model
        # declines to score some situations and the caller should see that.
        "xpass": {"value": mean(acc["xpass_sum"], acc["xpass_n"]),
                  "n": acc["xpass_n"],
                  "coverage": mean(acc["xpass_n"], acc["pass_oe_rows"] or n)},
        "pass_oe": {"value": mean(acc["pass_oe_sum"], acc["pass_oe_n"], 2),
                    "n": acc["pass_oe_n"],
                    "coverage": 1.0 if acc["pass_oe_n"] else None},
        "shotgun_rate": charted(acc["shotgun_hits"], acc["shotgun_n"], n),
        "no_huddle_rate": charted(acc["no_huddle_hits"], acc["no_huddle_n"], n),
        "personnel": {
            "mix": rate_table(acc["groupings"]),
            "n": acc["personnel_charted"],
            "coverage": mean(acc["personnel_charted"], n),
            "by_down": {k: rate_table(v) for k, v in sorted(acc["grouping_by_down"].items())},
            "by_distance": {k: rate_table(acc["grouping_by_distance"][k])
                            for k in ("short", "medium", "long")
                            if k in acc["grouping_by_distance"]},
            "by_field_zone": {k: rate_table(acc["grouping_by_field_zone"][k])
                              for k in ("backed_up", "own_territory",
                                        "opp_territory", "red_zone")
                              if k in acc["grouping_by_field_zone"]},
        },
        "formation": {
            "mix": rate_table(acc["formations"]),
            "n": acc["formation_charted"],
            "coverage": mean(acc["formation_charted"], n),
        },
        "ftn": ftn,
    }


def _league_playbook(session: Session, season: int, phase: str) -> dict[str, dict]:
    """Cached league-wide reduction -- one entry serves all 32 teams.

    Same invariant as compare._all_team_metrics: the cached value is plain
    scalars, dicts and lists only. It outlives the request that built it and
    that request's Session is closed on the way out, so an ORM instance or a
    Row in here would be a use-after-close bug on the next hit. _finalize is
    what enforces it -- the Counter/set-bearing accumulators never escape
    _compute_league_playbook.
    """
    return league_cache.get_or_set(
        ("playbook", season, phase),
        lambda: _compute_league_playbook(session, season, phase),
    )


def _staff_rows(session: Session, season: int) -> list[Row]:
    return session.execute(
        select(CoachingStaff.team, CoachingStaff.head_coach_id,
               CoachingStaff.offensive_play_caller_id, CoachingStaff.oc_id,
               CoachingStaff.verified, CoachingStaff.note)
        .where(CoachingStaff.season == season)
    ).all()


def _coach_names(session: Session, ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(session.execute(
        select(Coach.id, Coach.name).where(Coach.id.in_(ids))
    ).all())


def _caller_for(row: Row, names: dict[str, str]) -> dict:
    """Who called the offense, and how much to trust that.

    Falls back to the head coach where offensive_play_caller_id is NULL, which
    is the documented contract on CoachingStaff. Two separate reasons the
    result may be wrong travel with it:

      * `verified` is False on every coordinator row in the shipped
        coaches.yml -- those entries are model-generated, not sourced.
      * head coaches themselves are derived from nflverse schedules, which
        record one coach per team-season from 2024 on and miss in-season
        firings. NYJ 2024 attributes all 17 games to Saleh though Ulbrich
        coached 12 of them; `note` carries that where it is known.

    So this returns an attribution plus its caveats, never a bare name.
    """
    caller_id = row.offensive_play_caller_id or row.head_coach_id
    return {
        "coach_id": caller_id,
        "name": names.get(caller_id) if caller_id else None,
        "verified": bool(row.verified),
        "is_head_coach": bool(caller_id and caller_id == row.head_coach_id),
        "fell_back_to_head_coach": row.offensive_play_caller_id is None,
        "note": row.note,
    }


def team_playbook(session: Session, team: str, season: int, phase: str) -> dict:
    """One team's offensive playbook for a season."""
    league = _league_playbook(session, season, phase)
    mine = league.get(team)

    staff = {r.team: r for r in _staff_rows(session, season)}
    row = staff.get(team)
    names = _coach_names(session, {row.offensive_play_caller_id, row.head_coach_id}
                         if row else set())
    t = session.get(Team, team)

    return {
        "team": {"abbr": team, "name": t.name if t else team,
                 "color": t.color if t else None, "logo": t.logo if t else None},
        "season": season, "phase": phase,
        "play_caller": _caller_for(row, names) if row else None,
        "playbook": mine or _finalize(_blank(), season),
        "league_ranks": _ranks(league, team),
    }


def _ranks(league: dict[str, dict], team: str) -> dict:
    """Where this team sits league-wide on the handful of scalar tendencies.

    Tendency, not quality -- rank 1 is simply the highest value. Rendering
    these as good/bad would be a category error; a high no-huddle rate is a
    style, not a virtue.
    """
    out: dict[str, int | None] = {}
    for key, get in (
        ("pass_rate", lambda m: m.get("pass_rate")),
        ("pass_oe", lambda m: (m.get("pass_oe") or {}).get("value")),
        ("shotgun_rate", lambda m: (m.get("shotgun_rate") or {}).get("value")),
        ("no_huddle_rate", lambda m: (m.get("no_huddle_rate") or {}).get("value")),
        ("epa_per_play", lambda m: m.get("epa_per_play")),
    ):
        mine = get(league.get(team, {}))
        if mine is None:
            out[key] = None
            continue
        vals = sorted((v for v in (get(m) for m in league.values()) if v is not None),
                      reverse=True)
        out[key] = vals.index(mine) + 1 if mine in vals else None
    out["of"] = len(league)
    return out


def play_caller_playbook(session: Session, coach_id: str, season: int, phase: str) -> dict:
    """The same view aggregated over every team this caller ran in a season.

    Almost always one team; the exception is the fallback path, where a head
    coach with no named OC picks up his own offense. Merging accumulators
    rather than averaging rates is deliberate -- a 200-play team and a
    1000-play team must not count equally.
    """
    staff = _staff_rows(session, season)
    mine = [r for r in staff
            if (r.offensive_play_caller_id or r.head_coach_id) == coach_id]
    if not mine:
        raise ValueError(f"no {season} team lists {coach_id} as offensive play-caller")

    # Re-run the per-team reduction from the cached raw pass. The cache holds
    # finalized scalars, so merging has to go back through the accumulators;
    # this recomputes only for the (usually one) team involved.
    acc = _blank()
    for row in mine:
        acc = _merge(acc, _team_accumulator(session, row.team, season, phase))

    names = _coach_names(session, {coach_id} | {r.head_coach_id for r in mine})
    return {
        "coach": {"coach_id": coach_id, "name": names.get(coach_id)},
        "season": season, "phase": phase,
        "teams": [_caller_for(r, names) | {"team": r.team} for r in mine],
        "verified": all(bool(r.verified) for r in mine),
        "playbook": _finalize(acc, season),
    }


def _team_accumulator(session: Session, team: str, season: int, phase: str) -> dict:
    """Raw accumulator for one team. Not cached -- it holds sets and Counters."""
    rows = session.execute(
        select(Play.game_id, Play.posteam, Play.play_type, Play.down, Play.ydstogo,
               Play.yardline_100, Play.offense_personnel, Play.offense_formation,
               Play.shotgun, Play.no_huddle, Play.xpass, Play.pass_oe, Play.epa,
               Play.is_play_action, Play.is_motion, Play.is_screen_pass, Play.is_rpo)
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase, Play.posteam == team,
               or_(Play.play_type.in_(("pass", "run")),
                   Play.pass_oe.isnot(None)))
    ).all()
    acc = _blank()
    for p in rows:
        _accumulate_pass_oe(acc, p)
        if p.play_type in ("pass", "run"):
            _accumulate(acc, p)
    return acc
