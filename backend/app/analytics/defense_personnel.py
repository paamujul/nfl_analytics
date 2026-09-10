"""What a defense lines up in, who rushes, and who covers.

analytics/defense_profile.py answers "how good is this defense" -- percentile
rankings and an archetype label. This module answers the prior question: what
does it actually *do*. Personnel packages, box counts, pressure and blitz,
man/zone, coverage families, and the players behind each of those. The two are
kept apart rather than merged because they have different failure modes:
defense_profile reads dense columns (epa, sack, interception) and can be stated
flatly, while almost everything here comes off a charting feed with a fill rate
that moves by season, so it is stated with its sample.

Where the sample lives is worth being precise about, because it differs by
column even within one season (measured over 2024 regular-season pass/run
plays):

    defense_personnel   100%     complete enough to read as a tendency
    was_pressure        100% of 2023+ pass plays, ~53% of 2022, ~55% of 2021
    defense_man_zone    ~60% of scrimmage plays, all seasons
    defense_coverage    ~60%, same rows as man/zone

A man/zone split over a 60% sample is a split *among charted plays*. If the
charter systematically skips, say, garbage time, the number is biased and
nothing in the data reveals by how much. Hence the {"value", "n", "coverage"}
shape from analytics/personnel on every one of these.

Per-player pressure cannot come from `was_pressure`: that column says the
quarterback was pressured, not who did it. Player attribution comes from
`player_season_advanced` (PFR's prss / hrry / qbkd / bltz), which covers all
five seasons at season granularity. Mixing the two would double-count, so the
team view uses plays and the player view uses PFR, and each says which.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import Row, func, select
from sqlalchemy.orm import Session

from app.analytics.personnel import (
    charted, defense_grouping, defense_package, rate_table,
)
from app.cache import league_cache
from app.db.models import DepthChartEntry, Game, Play, PlayerSeasonAdvanced, Team

# Five or more rushers. Same threshold defense_profile.py uses for blitz_rate;
# kept identical on purpose so the two modules never disagree on screen.
BLITZ_RUSHERS = 5

# Minimum snaps before a player's PFR rates are worth showing. A corner with
# four targets can post a 0% missed-tackle rate and it means nothing.
MIN_GAMES_FOR_RATES = 4


def _blank() -> dict:
    return {
        "plays": 0,
        "games": set(),
        "groupings": Counter(),
        "packages": Counter(),
        "personnel_charted": 0,
        "box_sum": 0, "box_n": 0,
        "box_by_down": defaultdict(lambda: [0, 0]),
        "pass_plays": 0,
        "pressure_hits": 0, "pressure_n": 0,
        "blitz_hits": 0, "blitz_n": 0,
        "man": 0, "zone": 0, "man_zone_n": 0,
        "coverages": Counter(),
    }


def _accumulate(acc: dict, p: Row) -> None:
    acc["plays"] += 1
    acc["games"].add(p.game_id)

    grouping = defense_grouping(p.defense_personnel)
    if grouping:
        acc["personnel_charted"] += 1
        acc["groupings"][grouping] += 1
        pkg = defense_package(p.defense_personnel)
        if pkg:
            acc["packages"][pkg] += 1

    if p.defenders_in_box is not None:
        acc["box_sum"] += p.defenders_in_box
        acc["box_n"] += 1
        if p.down:
            slot = acc["box_by_down"][str(int(p.down))]
            slot[0] += p.defenders_in_box
            slot[1] += 1

    if p.play_type == "pass":
        acc["pass_plays"] += 1
        if p.was_pressure is not None:
            acc["pressure_n"] += 1
            acc["pressure_hits"] += 1 if p.was_pressure else 0
        if p.n_pass_rushers is not None:
            acc["blitz_n"] += 1
            acc["blitz_hits"] += 1 if p.n_pass_rushers >= BLITZ_RUSHERS else 0

    if p.defense_man_zone_type:
        acc["man_zone_n"] += 1
        if p.defense_man_zone_type.startswith("MAN"):
            acc["man"] += 1
        elif p.defense_man_zone_type.startswith("ZONE"):
            acc["zone"] += 1
    if p.defense_coverage_type:
        acc["coverages"][p.defense_coverage_type] += 1


def _finalize(acc: dict) -> dict:
    """Accumulator -> plain scalars. No Rows, no Sessions, no sets past here."""
    n = acc["plays"]

    def mean(total, count, digits=3):
        return round(total / count, digits) if count else None

    return {
        "plays": n,
        "games": len(acc["games"]),
        "personnel": {
            "mix": rate_table(acc["groupings"])[:8],
            "packages": rate_table(acc["packages"]),
            "n": acc["personnel_charted"],
            "coverage": mean(acc["personnel_charted"], n),
        },
        "defenders_in_box": {
            "value": mean(acc["box_sum"], acc["box_n"], 2),
            "n": acc["box_n"],
            "coverage": mean(acc["box_n"], n),
            "by_down": {d: {"value": mean(s[0], s[1], 2), "n": s[1]}
                        for d, s in sorted(acc["box_by_down"].items())},
        },
        # Pressure and blitz are pass-play statistics, so their coverage is
        # against pass plays -- against all scrimmage plays it would look like
        # a 60% fill rate on a column that is in fact complete.
        "pressure_rate": charted(acc["pressure_hits"], acc["pressure_n"],
                                 acc["pass_plays"]),
        "blitz_rate": charted(acc["blitz_hits"], acc["blitz_n"], acc["pass_plays"]),
        "man_rate": charted(acc["man"], acc["man_zone_n"], n),
        "zone_rate": charted(acc["zone"], acc["man_zone_n"], n),
        "coverage_types": {
            "mix": rate_table(acc["coverages"]),
            "n": sum(acc["coverages"].values()),
            "coverage": mean(sum(acc["coverages"].values()), n),
        },
    }


def _compute_league_defense_personnel(session: Session, season: int,
                                      phase: str) -> dict[str, dict]:
    # Column-only select: see the note in analytics/compare.py. The grouping
    # key (DL-LB-DB) has to be parsed out of defense_personnel in Python -- a
    # GROUP BY on the raw string yields ~400 distinct values per team -- but
    # every plain aggregate rides along in the same pass rather than costing a
    # second scan of a 250k-row table.
    rows = session.execute(
        select(Play.game_id, Play.defteam, Play.play_type, Play.down,
               Play.defense_personnel, Play.defenders_in_box, Play.was_pressure,
               Play.n_pass_rushers, Play.defense_man_zone_type,
               Play.defense_coverage_type)
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase,
               Play.play_type.in_(("pass", "run")))
    ).all()

    by_team: dict[str, dict] = {}
    for p in rows:
        if p.defteam:
            _accumulate(by_team.setdefault(p.defteam, _blank()), p)
    return {team: _finalize(acc) for team, acc in by_team.items()}


def _league_defense_personnel(session: Session, season: int, phase: str) -> dict[str, dict]:
    """Cached league-wide reduction -- one entry serves all 32 defenses.

    Same invariant as compare._all_team_metrics and defense_profile's: plain
    scalars only. _finalize is what enforces it; the accumulators hold a set of
    game ids and several Counters and none of them escape.
    """
    return league_cache.get_or_set(
        ("defense_personnel", season, phase),
        lambda: _compute_league_defense_personnel(session, season, phase),
    )


def _starters(session: Session, team: str, season: int) -> dict[str, list[dict]]:
    """Defensive starters by position group, from the weekly depth chart.

    depth_team = 1 is a starter. 2021-2024 publish a chart per week, 2025
    publishes undated snapshots at week 0 (see DepthChartEntry), so a player is
    counted a starter if he was listed first in any week and ranked by how many
    weeks that was -- which also survives the 2025 shape without a special case.
    """
    rows = session.execute(
        select(DepthChartEntry.player_id, DepthChartEntry.player_name,
               DepthChartEntry.position, DepthChartEntry.depth_position,
               func.count().label("weeks"))
        .where(DepthChartEntry.season == season, DepthChartEntry.team == team,
               DepthChartEntry.formation == "Defense",
               DepthChartEntry.depth_team == 1)
        .group_by(DepthChartEntry.player_id, DepthChartEntry.player_name,
                  DepthChartEntry.position, DepthChartEntry.depth_position)
    ).all()

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        pos = (r.position or r.depth_position or "").upper()
        unit = ("DL" if pos in {"DE", "DT", "NT", "DL"}
                else "LB" if pos in {"ILB", "OLB", "MLB", "LB"}
                else "DB" if pos in {"CB", "FS", "SS", "S", "DB"} else "other")
        groups[unit].append({
            "player_id": r.player_id, "name": r.player_name,
            "position": pos or None, "weeks_as_starter": r.weeks,
        })
    for unit in groups:
        groups[unit].sort(key=lambda p: (-p["weeks_as_starter"], p["name"] or ""))
    return dict(groups)


def _player_defense(session: Session, team: str, season: int) -> dict:
    """Per-player pass rush and tackling, from PFR advanced stats.

    Deliberately not derived from was_pressure: that column records that the
    quarterback was pressured, not who pressured him. PFR charts the player,
    covers all five backfilled seasons, and is the only source here that can
    answer "who". Its granularity is the season, so there is no weekly view.

    `team` is "2TM" upstream for a player traded mid-season; those rows are
    dropped rather than assigned, since PFR does not split the totals.
    """
    rows = session.execute(
        select(PlayerSeasonAdvanced.player_id, PlayerSeasonAdvanced.player_name,
               PlayerSeasonAdvanced.position, PlayerSeasonAdvanced.games,
               PlayerSeasonAdvanced.prss, PlayerSeasonAdvanced.hrry,
               PlayerSeasonAdvanced.qbkd, PlayerSeasonAdvanced.bltz,
               PlayerSeasonAdvanced.sk, PlayerSeasonAdvanced.comb,
               PlayerSeasonAdvanced.m_tkl_percent, PlayerSeasonAdvanced.tgt,
               PlayerSeasonAdvanced.cmp_percent, PlayerSeasonAdvanced.rat,
               PlayerSeasonAdvanced.dadot)
        .where(PlayerSeasonAdvanced.season == season,
               PlayerSeasonAdvanced.team == team)
    ).all()

    def block(r):
        thin = (r.games or 0) < MIN_GAMES_FOR_RATES
        return {
            "player_id": r.player_id, "name": r.player_name,
            "position": r.position, "games": r.games,
            "pressures": r.prss, "hurries": r.hrry, "qb_knockdowns": r.qbkd,
            "sacks": r.sk, "times_blitzed": r.bltz,
            "pressures_per_game": (round(r.prss / r.games, 2)
                                   if r.prss is not None and r.games else None),
            "tackles": r.comb,
            "missed_tackle_pct": r.m_tkl_percent,
            "targets": r.tgt, "completion_pct_allowed": r.cmp_percent,
            "passer_rating_allowed": r.rat, "avg_depth_of_target": r.dadot,
            # Rates off a handful of games are noise; say so rather than
            # letting a 1-game corner top the leaderboard.
            "small_sample": thin,
        }

    players = [block(r) for r in rows]
    rushers = sorted((p for p in players if p["pressures"] is not None),
                     key=lambda p: -p["pressures"])
    tacklers = sorted(
        (p for p in players
         if p["missed_tackle_pct"] is not None and not p["small_sample"]),
        key=lambda p: p["missed_tackle_pct"])
    return {
        "source": "pro-football-reference advanced defense (season granularity)",
        "players": sorted(players, key=lambda p: (p["position"] or "", p["name"] or "")),
        "top_pass_rushers": rushers[:8],
        "surest_tacklers": tacklers[:8],
        "most_missed_tackles": list(reversed(tacklers))[:5],
    }


def team_defense_personnel(session: Session, team: str, season: int, phase: str) -> dict:
    league = _league_defense_personnel(session, season, phase)
    mine = league.get(team) or _finalize(_blank())

    def rank(get):
        v = get(mine)
        if v is None:
            return None
        vals = sorted((x for x in (get(m) for m in league.values()) if x is not None),
                      reverse=True)
        return vals.index(v) + 1 if v in vals else None

    t = session.get(Team, team)
    return {
        "team": {"abbr": team, "name": t.name if t else team,
                 "color": t.color if t else None, "logo": t.logo if t else None},
        "season": season, "phase": phase,
        "defense": mine,
        "league_ranks": {
            "pressure_rate": rank(lambda m: m["pressure_rate"]["value"]),
            "blitz_rate": rank(lambda m: m["blitz_rate"]["value"]),
            "man_rate": rank(lambda m: m["man_rate"]["value"]),
            "defenders_in_box": rank(lambda m: m["defenders_in_box"]["value"]),
            "of": len(league),
        },
        "starters": _starters(session, team, season),
        "players": _player_defense(session, team, season),
    }
