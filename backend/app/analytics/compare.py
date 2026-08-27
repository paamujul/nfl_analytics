"""Head-to-head team comparison with league-percentile context."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Game, Play, Team


def _team_play_metrics(plays: list[Play], as_offense: bool) -> dict:
    scrim = [p for p in plays if p.play_type in ("pass", "run")]
    passes = [p for p in scrim if p.play_type == "pass"]
    runs = [p for p in scrim if p.play_type == "run"]
    games = len({p.game_id for p in scrim})

    def _avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    pass_yds = sum(p.yards_gained or 0 for p in passes)
    rush_yds = sum(p.yards_gained or 0 for p in runs)
    return {
        "games": games,
        "plays": len(scrim),
        "pass_yds_pg": round(pass_yds / games, 1) if games else 0,
        "rush_yds_pg": round(rush_yds / games, 1) if games else 0,
        "yds_per_play": round((pass_yds + rush_yds) / len(scrim), 2) if scrim else 0,
        "pass_epa": _avg([p.epa for p in passes]),
        "rush_epa": _avg([p.epa for p in runs]),
        "success_rate": _avg([1.0 if p.success else 0.0 for p in scrim if p.success is not None]),
        "pass_rate": round(len(passes) / len(scrim), 3) if scrim else None,
        "sack_rate": round(sum(1 for p in passes if p.sack) / len(passes), 3) if passes else None,
        "explosive_rate": _avg([
            1.0 if ((p.yards_gained or 0) >= (20 if p.play_type == "pass" else 10)) else 0.0
            for p in scrim]),
    }


def _all_team_metrics(session: Session, season: int, phase: str) -> dict[str, dict]:
    plays = session.scalars(
        select(Play).join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase,
               Play.play_type.in_(("pass", "run")))
    ).all()
    by_off: dict[str, list[Play]] = {}
    by_def: dict[str, list[Play]] = {}
    for p in plays:
        if p.posteam:
            by_off.setdefault(p.posteam, []).append(p)
        if p.defteam:
            by_def.setdefault(p.defteam, []).append(p)
    return {
        team: {
            "offense": _team_play_metrics(by_off.get(team, []), True),
            "defense": _team_play_metrics(by_def.get(team, []), False),
        }
        for team in set(by_off) | set(by_def)
    }


def _percentile(values: list[float], v: float | None, higher_is_better: bool) -> int | None:
    vals = sorted(x for x in values if x is not None)
    if v is None or not vals:
        return None
    below = sum(1 for x in vals if x < v) + 0.5 * sum(1 for x in vals if x == v)
    pct = below / len(vals)
    if not higher_is_better:
        pct = 1 - pct
    return round(pct * 100)


# metric key -> (label, higher_is_better for OFFENSE)
COMPARE_METRICS = {
    "pass_yds_pg": ("Pass yards / game", True),
    "rush_yds_pg": ("Rush yards / game", True),
    "yds_per_play": ("Yards / play", True),
    "pass_epa": ("Pass EPA / play", True),
    "rush_epa": ("Rush EPA / play", True),
    "success_rate": ("Success rate", True),
    "explosive_rate": ("Explosive play rate", True),
    "pass_rate": ("Pass rate", True),  # tendency, not quality — percentile still useful
}


def compare_teams(session: Session, team_a: str, team_b: str,
                  season: int, phase: str) -> dict:
    league = _all_team_metrics(session, season, phase)
    ta, tb = session.get(Team, team_a), session.get(Team, team_b)

    def team_block(abbr: str, t: Team | None) -> dict:
        m = league.get(abbr, {"offense": {}, "defense": {}})
        return {
            "abbr": abbr, "name": t.name if t else abbr,
            "color": t.color if t else None, "logo": t.logo if t else None,
            "offense": m["offense"], "defense": m["defense"],
        }

    metrics = []
    for key, (label, off_better) in COMPARE_METRICS.items():
        off_vals = [m["offense"].get(key) for m in league.values() if m["offense"]]
        def_vals = [m["defense"].get(key) for m in league.values() if m["defense"]]
        a_off = league.get(team_a, {}).get("offense", {}).get(key)
        b_off = league.get(team_b, {}).get("offense", {}).get(key)
        a_def = league.get(team_a, {}).get("defense", {}).get(key)
        b_def = league.get(team_b, {}).get("defense", {}).get(key)
        neutral = key == "pass_rate"
        metrics.append({
            "key": key, "label": label,
            "offense": {
                "a": a_off, "b": b_off,
                "a_pct": _percentile(off_vals, a_off, off_better),
                "b_pct": _percentile(off_vals, b_off, off_better),
                "neutral": neutral,
            },
            # for defense, allowing more of a good-for-offense thing is bad
            "defense": {
                "a": a_def, "b": b_def,
                "a_pct": _percentile(def_vals, a_def, not off_better),
                "b_pct": _percentile(def_vals, b_def, not off_better),
                "neutral": neutral,
            },
        })

    return {
        "season": season, "phase": phase,
        "a": team_block(team_a, ta), "b": team_block(team_b, tb),
        "metrics": metrics,
    }
