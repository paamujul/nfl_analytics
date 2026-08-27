"""Per-quarter player performance: which quarter does a player do damage in."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Game, Play, Player, PlayerGameStat


def player_quarter_splits(session: Session, player_id: str, season: int,
                          phase: str, game_id: str | None = None) -> dict:
    player = session.get(Player, player_id)

    q = (
        select(Play, Game.week)
        .join(Game, Game.id == Play.game_id)
        .where(Game.season == season, Game.phase == phase)
        .where(or_(Play.passer_id == player_id,
                   Play.rusher_id == player_id,
                   Play.receiver_id == player_id))
    )
    if game_id:
        q = q.where(Play.game_id == game_id)
    rows = session.execute(q).all()

    quarters: dict[int, dict] = {}
    for play, _week in rows:
        qtr = play.quarter or 0
        b = quarters.setdefault(qtr, {
            "quarter": qtr, "plays": 0,
            "pass_att": 0, "pass_cmp": 0, "pass_yds": 0.0, "pass_td": 0,
            "rush_att": 0, "rush_yds": 0.0, "rush_td": 0,
            "targets": 0, "receptions": 0, "rec_yds": 0.0, "rec_td": 0,
            "epa": 0.0, "epa_n": 0, "success": 0, "success_n": 0,
        })
        b["plays"] += 1
        yards = play.yards_gained or 0.0
        is_td = bool(play.touchdown)

        if play.passer_id == player_id and play.play_type == "pass":
            if not play.sack:
                b["pass_att"] += 1
            if play.complete_pass:
                b["pass_cmp"] += 1
                b["pass_yds"] += yards
                if is_td:
                    b["pass_td"] += 1
        if play.rusher_id == player_id and play.play_type == "run":
            b["rush_att"] += 1
            b["rush_yds"] += yards
            if is_td:
                b["rush_td"] += 1
        if play.receiver_id == player_id:
            b["targets"] += 1
            if play.complete_pass:
                b["receptions"] += 1
                b["rec_yds"] += yards
                if is_td:
                    b["rec_td"] += 1

        if play.epa is not None:
            b["epa"] += play.epa
            b["epa_n"] += 1
        if play.success is not None:
            b["success"] += 1 if play.success else 0
            b["success_n"] += 1

    out = []
    for qtr in sorted(quarters):
        b = quarters[qtr]
        total_yds = b["pass_yds"] + b["rush_yds"] + b["rec_yds"]
        out.append({
            **{k: (round(v, 1) if isinstance(v, float) else v)
               for k, v in b.items() if k not in ("epa", "epa_n", "success", "success_n")},
            "total_yds": round(total_yds, 1),
            "epa_per_play": round(b["epa"] / b["epa_n"], 3) if b["epa_n"] else None,
            "success_rate": round(b["success"] / b["success_n"], 3) if b["success_n"] else None,
        })

    best = None
    scored = [q for q in out if 1 <= q["quarter"] <= 5]
    if scored:
        best_q = max(scored, key=lambda x: (x["total_yds"], x["epa_per_play"] or 0))
        if best_q["total_yds"] > 0:
            best = best_q["quarter"]

    # which games exist for the game-selector dropdown
    games = session.execute(
        select(Game.id, Game.week, Game.home_team, Game.away_team, Game.kickoff)
        .join(PlayerGameStat, PlayerGameStat.game_id == Game.id)
        .where(PlayerGameStat.player_id == player_id,
               Game.season == season, Game.phase == phase)
        .order_by(Game.kickoff)
    ).all()

    return {
        "player": {
            "id": player_id,
            "name": player.name if player else player_id,
            "position": player.position if player else None,
            "team": player.team if player else None,
            "headshot": player.headshot if player else None,
        },
        "season": season, "phase": phase, "game_id": game_id,
        "quarters": out,
        "best_quarter": best,
        "games": [{"game_id": g, "week": w, "home": h, "away": a, "kickoff": k}
                  for g, w, h, a, k in games],
    }
