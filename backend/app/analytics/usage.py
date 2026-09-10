"""Who gets the ball, how often, and whether that changed over the season.

A season-total target share hides the thing a coach actually wants to know.
A receiver at 22% for the year could have been the WR1 all season, or a WR3 who
took over in week 10 when someone tore an ACL, and those are opposite reports.
So every share here is produced weekly first and the season total is a
by-product, and `trajectory` -- the weekly series plus a first-half/second-half
split -- is a first-class field rather than a drill-down.

Four denominators, deliberately not interchangeable:

  targets   plays where this player is the charted receiver / team pass plays
  carries   plays where this player is the charted rusher / team run plays
  snaps     snap_counts.offense_snaps / the team's offensive snaps that week
  routes    see the warning on `route_mix` below

The route caveat is the important one. `Play.route` is the route run by the
*targeted* receiver on that play -- there is one route per play, not one per
eligible receiver. It therefore cannot produce a route *share* (what fraction
of a team's routes a player ran); the participation data needed for that is not
in this database. What it can produce is a route *mix*: given that a player was
targeted, what was he running. That is a genuinely useful scouting number and a
completely different statistic, so it is named differently and carries its own
sample count.

snap_counts keys on `player_name`, not a gsis id, and ~6% of its names do not
match `players.name` exactly (suffixes, "George Karlaftis III"). Rather than
fuzzy-matching, unresolved rows keep their name and report player_id: null.

Unlike the other three modules in this package this one has no entry in
app/cache.py, because it has no league-wide reduction to cache. Every query
below is already scoped to one team and pushed into a GROUP BY, so a cold call
is ~130ms against the full backfill -- caching would add a stale-data window in
exchange for nothing.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import Row, func, select
from sqlalchemy.orm import Session

from app.analytics.personnel import rate_table
from app.db.models import Game, Play, Player, SnapCount, Team

# A player must clear this to be named a "primary" receiver or the lead back.
# Below it the label is noise -- a 4%-share back on a committee is not a lead
# back and calling him one is worse than saying nothing.
PRIMARY_TARGET_SHARE = 0.15
LEAD_BACK_CARRY_SHARE = 0.40


def _play_usage(session: Session, team: str, season: int, phase: str) -> dict:
    """Weekly target / carry / route counts from the plays table.

    Aggregated in SQL: this is a pure GROUP BY over (week, player) and pulling
    ~1000 raw rows per team to count them in Python would be pointless work
    against a 250k-row table. Three passes rather than one because the grouping
    column differs (receiver / rusher) and a single query would need a UNION
    that reads no better than this.
    """
    def counts(id_col, play_type):
        return session.execute(
            select(Game.week, id_col, func.count().label("n"))
            .select_from(Play)
            .join(Game, Game.id == Play.game_id)
            .where(Game.season == season, Game.phase == phase,
                   Play.posteam == team, Play.play_type == play_type,
                   id_col.isnot(None))
            .group_by(Game.week, id_col)
        ).all()

    def team_totals(play_type):
        return dict(session.execute(
            select(Game.week, func.count())
            .select_from(Play)
            .join(Game, Game.id == Play.game_id)
            .where(Game.season == season, Game.phase == phase,
                   Play.posteam == team, Play.play_type == play_type)
            .group_by(Game.week)
        ).all())

    routes = session.execute(
        select(Play.receiver_id, Play.route, func.count())
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase,
               Play.posteam == team, Play.receiver_id.isnot(None),
               Play.route.isnot(None))
        .group_by(Play.receiver_id, Play.route)
    ).all()

    # Targets that were charted with a route at all, per player -- the
    # denominator that makes route_mix legible.
    targets_by_player = dict(session.execute(
        select(Play.receiver_id, func.count())
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase,
               Play.posteam == team, Play.play_type == "pass",
               Play.receiver_id.isnot(None))
        .group_by(Play.receiver_id)
    ).all())

    route_mix: dict[str, Counter] = defaultdict(Counter)
    for pid, route, n in routes:
        route_mix[pid][route] += n

    return {
        "targets": counts(Play.receiver_id, "pass"),
        "carries": counts(Play.rusher_id, "run"),
        "team_pass": team_totals("pass"),
        "team_run": team_totals("run"),
        "route_mix": route_mix,
        "targets_by_player": targets_by_player,
    }


def _snap_usage(session: Session, team: str, season: int, phase: str) -> dict:
    """Weekly snap counts. Regular season only -- snap_counts has no phase column.

    The table is keyed (game_id, player_name, team) and carries its own
    `season`/`week`, so it is queried on those rather than joined to games.
    For a non-reg phase this simply returns nothing, which is honest: PFR does
    not publish preseason snap counts.
    """
    if phase != "reg":
        return {"rows": [], "team_off": {}, "team_def": {}, "available": False}

    rows = session.execute(
        select(SnapCount.week, SnapCount.player_name, SnapCount.position,
               SnapCount.offense_snaps, SnapCount.offense_pct,
               SnapCount.defense_snaps, SnapCount.defense_pct)
        .where(SnapCount.season == season, SnapCount.team == team)
    ).all()

    # A team's offensive snaps in a week is the max over its players, not the
    # sum: eleven players are on the field for each one.
    team_off: dict[int, int] = defaultdict(int)
    team_def: dict[int, int] = defaultdict(int)
    for r in rows:
        team_off[r.week] = max(team_off[r.week], r.offense_snaps or 0)
        team_def[r.week] = max(team_def[r.week], r.defense_snaps or 0)
    return {"rows": rows, "team_off": dict(team_off), "team_def": dict(team_def),
            "available": True}


def _series(weekly: dict[int, tuple[int, int]]) -> dict:
    """Weekly (hits, denominator) -> a share series plus a half-season split."""
    weeks = sorted(weekly)
    points = [{"week": w,
               "n": weekly[w][0],
               "of": weekly[w][1],
               "share": round(weekly[w][0] / weekly[w][1], 3) if weekly[w][1] else None}
              for w in weeks]
    # A single week has no halves. Splitting it anyway would report the same
    # number as both, and a trend of exactly 0.0 for a player with one game --
    # which reads as "stable" and is the opposite of what the data says.
    if len(weeks) < 2:
        return {"weeks": points, "first_half": None, "second_half": None,
                "trend": None}
    mid = len(weeks) // 2

    def half(ws):
        hits = sum(weekly[w][0] for w in ws)
        of = sum(weekly[w][1] for w in ws)
        return round(hits / of, 3) if of else None

    first, second = half(weeks[:mid]), half(weeks[mid:])
    trend = (round(second - first, 3)
             if first is not None and second is not None else None)
    return {"weeks": points, "first_half": first, "second_half": second,
            "trend": trend}


def _resolve_names(session: Session, ids: set[str]) -> dict[str, Row]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {p.id: p for p in session.execute(
        select(Player.id, Player.name, Player.position, Player.headshot)
        .where(Player.id.in_(ids))
    ).all()}


def _name_to_id(session: Session, team: str, names: set[str]) -> dict[str, str]:
    """Best-effort snap_counts name -> gsis id. Unmatched names stay unmatched."""
    if not names:
        return {}
    return dict(session.execute(
        select(Player.name, Player.id)
        .where(Player.team == team, Player.name.in_(names))
    ).all())


def team_usage(session: Session, team: str, season: int, phase: str) -> dict:
    plays = _play_usage(session, team, season, phase)
    snaps = _snap_usage(session, team, season, phase)

    tgt: dict[str, dict[int, tuple[int, int]]] = defaultdict(dict)
    for week, pid, n in plays["targets"]:
        tgt[pid][week] = (n, plays["team_pass"].get(week, 0))
    car: dict[str, dict[int, tuple[int, int]]] = defaultdict(dict)
    for week, pid, n in plays["carries"]:
        car[pid][week] = (n, plays["team_run"].get(week, 0))

    snap_weekly: dict[str, dict[int, tuple[int, int]]] = defaultdict(dict)
    snap_meta: dict[str, dict] = {}
    for r in snaps["rows"]:
        if not r.offense_snaps:
            continue
        snap_weekly[r.player_name][r.week] = (r.offense_snaps,
                                              snaps["team_off"].get(r.week, 0))
        snap_meta.setdefault(r.player_name, {"position": r.position})

    ids = set(tgt) | set(car)
    people = _resolve_names(session, ids)
    by_name = _name_to_id(session, team, set(snap_weekly))
    id_to_snapname = {v: k for k, v in by_name.items()}

    season_pass = sum(plays["team_pass"].values())
    season_run = sum(plays["team_run"].values())

    players = []
    for pid in ids:
        p = people.get(pid)
        t_total = sum(v[0] for v in tgt.get(pid, {}).values())
        c_total = sum(v[0] for v in car.get(pid, {}).values())
        snap_name = id_to_snapname.get(pid)
        mix = plays["route_mix"].get(pid, Counter())
        players.append({
            "player_id": pid,
            "name": p.name if p else pid,
            "position": p.position if p else None,
            "headshot": p.headshot if p else None,
            "targets": t_total,
            "target_share": round(t_total / season_pass, 3) if season_pass else None,
            "carries": c_total,
            "carry_share": round(c_total / season_run, 3) if season_run else None,
            "target_trajectory": _series(tgt.get(pid, {})),
            "carry_trajectory": _series(car.get(pid, {})),
            "snap_trajectory": (_series(snap_weekly[snap_name])
                                if snap_name in snap_weekly else None),
            "snap_share": (round(
                sum(v[0] for v in snap_weekly[snap_name].values())
                / max(1, sum(v[1] for v in snap_weekly[snap_name].values())), 3)
                if snap_name in snap_weekly else None),
            # Route MIX, not route share -- Play.route is the targeted
            # receiver's route, so the denominator is this player's charted
            # targets, not the team's routes. See the module docstring.
            "route_mix": {
                "mix": rate_table(mix)[:10],
                "n": sum(mix.values()),
                "coverage": (round(sum(mix.values())
                                   / plays["targets_by_player"].get(pid, 1), 3)
                             if plays["targets_by_player"].get(pid) else None),
            },
        })

    players.sort(key=lambda r: (-(r["targets"] + r["carries"]), r["name"]))

    primary = [r for r in players
               if (r["target_share"] or 0) >= PRIMARY_TARGET_SHARE]
    backs = [r for r in players if (r["carry_share"] or 0) >= LEAD_BACK_CARRY_SHARE]

    # Players who took snaps but never touched the ball -- linemen, blocking
    # tight ends. Reported separately so the main list stays about usage.
    unresolved = sorted(n for n in snap_weekly if n not in by_name)

    t = session.get(Team, team)
    return {
        "team": {"abbr": team, "name": t.name if t else team,
                 "color": t.color if t else None, "logo": t.logo if t else None},
        "season": season, "phase": phase,
        "team_pass_plays": season_pass,
        "team_run_plays": season_run,
        "snap_counts_available": snaps["available"],
        "players": players,
        "primary_receivers": [
            {"player_id": r["player_id"], "name": r["name"],
             "target_share": r["target_share"], "trend": r["target_trajectory"]["trend"]}
            for r in sorted(primary, key=lambda r: -(r["target_share"] or 0))[:3]
        ],
        "lead_back": (
            {"player_id": backs[0]["player_id"], "name": backs[0]["name"],
             "carry_share": backs[0]["carry_share"],
             "trend": backs[0]["carry_trajectory"]["trend"]}
            if backs else None
        ),
        "backfield_is_committee": not backs and season_run > 0,
        "snap_names_unresolved": unresolved,
    }
