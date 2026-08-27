"""Lineup impact: how a combination of players on the field changes outcomes.

Defense side: with the selected defenders all on the field, does the
opposing offense gain more passing or rushing yards (vs snaps without the
full combo)? Offense side: with the selected players on the field, does the
offense lean pass or run, and how efficient is it?

Play-level analysis uses nflverse participation (exact on-field players per
play — available for completed seasons like 2025). When participation is
missing (2026 in-season), falls back to game-level snap-count presence.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LINEUP_MIN_PLAYS
from app.db.models import Game, Play, Player, SnapCount

DEFENSE_POS = {"DE", "DT", "NT", "DL", "EDGE", "LB", "ILB", "OLB", "MLB",
               "CB", "S", "SS", "FS", "DB"}
OFFENSE_POS = {"QB", "RB", "FB", "WR", "TE", "T", "G", "C", "OL", "OT", "OG"}


def _split_metrics(plays: list[Play]) -> dict:
    passes = [p for p in plays if p.play_type == "pass"]
    runs = [p for p in plays if p.play_type == "run"]

    def _avg(vals, digits=3):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), digits) if vals else None

    n = len(passes) + len(runs)
    return {
        "plays": n, "pass_plays": len(passes), "run_plays": len(runs),
        "pass_rate": round(len(passes) / n, 3) if n else None,
        "pass_yds_per_play": _avg([p.yards_gained for p in passes], 2),
        "rush_yds_per_play": _avg([p.yards_gained for p in runs], 2),
        "pass_epa": _avg([p.epa for p in passes]),
        "rush_epa": _avg([p.epa for p in runs]),
        "epa_per_play": _avg([p.epa for p in plays]),
        "success_rate": _avg([1.0 if p.success else 0.0
                              for p in plays if p.success is not None]),
    }


def _verdict(side: str, on: dict, off: dict) -> str:
    """Plain-language answer to 'what does this combo do?'"""
    if not on["plays"] or not off["plays"]:
        return "Not enough data for a verdict."
    parts = []
    d_pass = (on["pass_yds_per_play"] or 0) - (off["pass_yds_per_play"] or 0)
    d_rush = (on["rush_yds_per_play"] or 0) - (off["rush_yds_per_play"] or 0)
    if side == "defense":
        subj = "With this group on the field, opponents"
        if abs(d_pass) >= 0.3:
            parts.append(f"{subj} gain {abs(d_pass):.1f} {'more' if d_pass > 0 else 'fewer'} passing yards per play")
        if abs(d_rush) >= 0.3:
            parts.append(f"{'and ' if parts else subj + ' gain '}{abs(d_rush):.1f} {'more' if d_rush > 0 else 'fewer'} rushing yards per play")
        d_pr = (on["pass_rate"] or 0) - (off["pass_rate"] or 0)
        if abs(d_pr) >= 0.04:
            parts.append(f"opponents {'throw' if d_pr > 0 else 'run'} noticeably more against this group")
    else:
        d_pr = (on["pass_rate"] or 0) - (off["pass_rate"] or 0)
        if abs(d_pr) >= 0.04:
            parts.append(f"With this group on the field the offense leans {'pass' if d_pr > 0 else 'run'} "
                         f"({(on['pass_rate'] or 0) * 100:.0f}% pass vs {(off['pass_rate'] or 0) * 100:.0f}% without them)")
        else:
            parts.append("This group doesn't meaningfully change the pass/run mix")
        d_epa = (on["epa_per_play"] or 0) - (off["epa_per_play"] or 0)
        if abs(d_epa) >= 0.03:
            parts.append(f"the offense is {'more' if d_epa > 0 else 'less'} efficient with them out there "
                         f"({d_epa:+.3f} EPA/play)")
    return ("; ".join(parts) + ".") if parts else "No meaningful difference detected."


def lineup_impact(session: Session, team: str, side: str, player_ids: list[str],
                  season: int, phase: str) -> dict:
    players = [session.get(Player, pid) for pid in player_ids]
    names = [{"id": pid, "name": p.name if p else pid,
              "position": p.position if p else None} for pid, p in zip(player_ids, players)]

    team_col = Play.defteam if side == "defense" else Play.posteam
    part_col = "defense_players" if side == "defense" else "offense_players"
    plays = session.scalars(
        select(Play).join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase,
               team_col == team, Play.play_type.in_(("pass", "run")))
    ).all()

    with_part = [p for p in plays if getattr(p, part_col)]
    if with_part:
        on, off = [], []
        for p in with_part:
            on_field = set((getattr(p, part_col) or "").split(";"))
            (on if all(pid in on_field for pid in player_ids) else off).append(p)
        on_m, off_m = _split_metrics(on), _split_metrics(off)
        ok = on_m["plays"] >= LINEUP_MIN_PLAYS and off_m["plays"] >= LINEUP_MIN_PLAYS
        return {
            "team": team, "side": side, "season": season, "phase": phase,
            "players": names, "method": "play_level",
            "on": on_m, "off": off_m,
            "sufficient": ok,
            "verdict": _verdict(side, on_m, off_m) if ok else
                f"Small sample ({on_m['plays']} plays together, {off_m['plays']} apart) — treat with caution. "
                + _verdict(side, on_m, off_m),
        }

    # ---- game-level fallback via snap counts (live 2026 season) ----
    snap_pct = SnapCount.defense_pct if side == "defense" else SnapCount.offense_pct
    snaps = session.execute(
        select(SnapCount.game_id, SnapCount.player_name, snap_pct)
        .where(SnapCount.team == team, SnapCount.season == season)
    ).all()
    by_game: dict[str, dict[str, float]] = {}
    for gid, pname, pct in snaps:
        by_game.setdefault(gid, {})[pname] = pct or 0.0

    sel_names = [n["name"] for n in names]
    on_games, off_games = [], []
    for gid, roster in by_game.items():
        if all(roster.get(nm, 0.0) >= 0.4 for nm in sel_names):
            on_games.append(gid)
        else:
            off_games.append(gid)

    def _games_metrics(game_ids: list[str]) -> dict:
        rows = [p for p in plays if p.game_id in set(game_ids)]
        m = _split_metrics(rows)
        m["games"] = len(game_ids)
        return m

    on_m, off_m = _games_metrics(on_games), _games_metrics(off_games)
    ok = len(on_games) >= 2 and len(off_games) >= 2
    return {
        "team": team, "side": side, "season": season, "phase": phase,
        "players": names, "method": "game_level",
        "on": on_m, "off": off_m,
        "sufficient": ok,
        "verdict": (_verdict(side, on_m, off_m) if ok else
                    "Per-play participation isn't published for this season yet, and there aren't "
                    f"enough games with/without this full group ({len(on_games)} vs {len(off_games)}) "
                    "for a game-level comparison."),
    }


def roster_for_side(session: Session, team: str, side: str, season: int,
                    phase: str) -> list[dict]:
    """Players selectable in the lineup builder, ranked by involvement."""
    wanted = DEFENSE_POS if side == "defense" else OFFENSE_POS
    players = session.scalars(
        select(Player).where(Player.team == team, Player.position.in_(wanted))
    ).all()

    # rank by snap participation if we have it
    snap_pct = SnapCount.defense_pct if side == "defense" else SnapCount.offense_pct
    snap_rows = session.execute(
        select(SnapCount.player_name, snap_pct)
        .where(SnapCount.team == team, SnapCount.season == season)
    ).all()
    avg_snaps: dict[str, list[float]] = {}
    for name, pct in snap_rows:
        avg_snaps.setdefault(name, []).append(pct or 0.0)

    out = []
    for p in players:
        pcts = avg_snaps.get(p.name, [])
        out.append({
            "id": p.id, "name": p.name, "position": p.position,
            "headshot": p.headshot,
            "avg_snap_pct": round(sum(pcts) / len(pcts), 3) if pcts else None,
        })
    out.sort(key=lambda r: (r["avg_snap_pct"] or 0), reverse=True)
    return out
