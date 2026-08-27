"""Defense identity: what kind of defense does a team play?

Statistical profile vs the rest of the league plus a readable archetype
label ("blitz-heavy, strong against the pass, vulnerable on the ground").
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Game, Play, Team


def _metrics_for(plays: list[Play]) -> dict:
    passes = [p for p in plays if p.play_type == "pass"]
    runs = [p for p in plays if p.play_type == "run"]
    games = len({p.game_id for p in plays})

    def _avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    blitz_known = [p for p in passes if p.n_pass_rushers is not None]
    return {
        "games": games,
        "pass_epa_allowed": _avg([p.epa for p in passes]),
        "rush_epa_allowed": _avg([p.epa for p in runs]),
        "success_allowed": _avg([1.0 if p.success else 0.0 for p in plays if p.success is not None]),
        "pass_yds_pg_allowed": round(sum(p.yards_gained or 0 for p in passes) / games, 1) if games else None,
        "rush_yds_pg_allowed": round(sum(p.yards_gained or 0 for p in runs) / games, 1) if games else None,
        "sack_rate": round(sum(1 for p in passes if p.sack) / len(passes), 3) if passes else None,
        "int_rate": round(sum(1 for p in passes if p.interception) / len(passes), 3) if passes else None,
        "explosive_pass_allowed": _avg([1.0 if (p.yards_gained or 0) >= 20 else 0.0 for p in passes]),
        "explosive_rush_allowed": _avg([1.0 if (p.yards_gained or 0) >= 10 else 0.0 for p in runs]),
        "stuff_rate": _avg([1.0 if (p.yards_gained or 0) <= 0 else 0.0 for p in runs]),
        "blitz_rate": (round(sum(1 for p in blitz_known if p.n_pass_rushers >= 5) / len(blitz_known), 3)
                       if blitz_known else None),
    }


# key -> (label, higher value is BETTER for the defense)
DEFENSE_METRICS = {
    "pass_epa_allowed": ("Pass EPA/play allowed", False),
    "rush_epa_allowed": ("Rush EPA/play allowed", False),
    "success_allowed": ("Success rate allowed", False),
    "pass_yds_pg_allowed": ("Pass yards/game allowed", False),
    "rush_yds_pg_allowed": ("Rush yards/game allowed", False),
    "sack_rate": ("Sack rate", True),
    "int_rate": ("Interception rate", True),
    "explosive_pass_allowed": ("Explosive passes allowed", False),
    "explosive_rush_allowed": ("Explosive runs allowed", False),
    "stuff_rate": ("Run stuff rate", True),
    "blitz_rate": ("Blitz rate", None),  # identity, not quality
}


def _percentile(values: list[float], v: float | None, higher_is_better: bool | None) -> int | None:
    vals = sorted(x for x in values if x is not None)
    if v is None or not vals:
        return None
    below = sum(1 for x in vals if x < v) + 0.5 * sum(1 for x in vals if x == v)
    pct = below / len(vals)
    if higher_is_better is False:
        pct = 1 - pct
    return round(pct * 100)


def _archetype(pcts: dict[str, int | None]) -> tuple[str, list[str]]:
    notes: list[str] = []

    def p(key: str) -> int:
        return pcts.get(key) if pcts.get(key) is not None else 50

    blitz = p("blitz_rate")
    if blitz >= 70:
        notes.append("blitz-heavy — sends five or more rushers far more than most teams")
    elif blitz <= 30:
        notes.append("rush-four defense — relies on coverage over pressure")

    pass_d = (p("pass_epa_allowed") + p("explosive_pass_allowed") + p("sack_rate")) / 3
    run_d = (p("rush_epa_allowed") + p("stuff_rate") + p("explosive_rush_allowed")) / 3
    if pass_d >= 65:
        notes.append("strong against the pass")
    elif pass_d <= 35:
        notes.append("vulnerable through the air")
    if run_d >= 65:
        notes.append("stout run defense")
    elif run_d <= 35:
        notes.append("can be run on")

    if p("sack_rate") >= 75:
        notes.append("elite pass rush")
    if p("int_rate") >= 75:
        notes.append("ball-hawking secondary")
    if p("explosive_pass_allowed") >= 70 and p("success_allowed") <= 45:
        notes.append("bend-but-don't-break — gives up completions but few explosives")

    overall = (pass_d + run_d) / 2
    tier = ("elite" if overall >= 80 else "above-average" if overall >= 60
            else "average" if overall >= 40 else "below-average" if overall >= 20
            else "struggling")
    headline = f"{tier.capitalize()} defense"
    if notes:
        headline += ": " + ", ".join(notes[:3])
    return headline, notes


def defense_profile(session: Session, team: str, season: int, phase: str) -> dict:
    plays = session.scalars(
        select(Play).join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase,
               Play.play_type.in_(("pass", "run")))
    ).all()

    by_def: dict[str, list[Play]] = {}
    for p in plays:
        if p.defteam:
            by_def.setdefault(p.defteam, []).append(p)

    league = {abbr: _metrics_for(pl) for abbr, pl in by_def.items()}
    mine = league.get(team, _metrics_for([]))

    rows = []
    pcts: dict[str, int | None] = {}
    for key, (label, better) in DEFENSE_METRICS.items():
        vals = [m.get(key) for m in league.values()]
        pct = _percentile(vals, mine.get(key), better)
        pcts[key] = pct
        rows.append({"key": key, "label": label, "value": mine.get(key),
                     "percentile": pct, "identity_only": better is None})

    headline, notes = _archetype(pcts)
    t = session.get(Team, team)
    return {
        "team": {"abbr": team, "name": t.name if t else team,
                 "color": t.color if t else None, "logo": t.logo if t else None},
        "season": season, "phase": phase,
        "games": mine.get("games", 0),
        "headline": headline, "notes": notes,
        "metrics": rows,
    }
