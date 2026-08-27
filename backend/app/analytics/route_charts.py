"""Route/target chart data for one player in one game.

Built from play outcomes (pass direction, air yards, catch point, YAC) —
the NFL's true tracking data isn't public, so this is an honest
approximation: every target the player saw, overlaid on a field, with the
frequent + successful directions highlighted by the frontend.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Game, Play, Player

DEPTH_DEFAULT = {"short": 6.0, "deep": 22.0}  # ESPN plays carry no air-yards number


def player_routes(session: Session, player_id: str, game_id: str) -> dict:
    player = session.get(Player, player_id)
    game = session.get(Game, game_id)

    targets = session.scalars(
        select(Play).where(Play.game_id == game_id, Play.receiver_id == player_id)
        .order_by(Play.play_id)
    ).all()
    carries = session.scalars(
        select(Play).where(Play.game_id == game_id, Play.rusher_id == player_id,
                           Play.play_type == "run")
        .order_by(Play.play_id)
    ).all()

    routes = []
    for p in targets:
        depth = p.air_yards
        if depth is None and p.pass_depth:
            depth = DEPTH_DEFAULT.get(p.pass_depth)
        routes.append({
            "play_id": p.play_id, "quarter": p.quarter,
            "location": p.pass_location,            # left | middle | right
            "depth": depth,                          # air yards (approx for ESPN)
            "depth_band": p.pass_depth,
            "complete": bool(p.complete_pass),
            "yards": p.yards_gained, "yac": p.yac,
            "touchdown": p.touchdown, "epa": p.epa,
            "success": p.success if p.success is not None else
                (bool(p.complete_pass) and (p.yards_gained or 0) > 0),
            "desc": p.desc,
        })

    runs = [{
        "play_id": p.play_id, "quarter": p.quarter,
        "location": p.run_location, "gap": p.run_gap,
        "yards": p.yards_gained, "touchdown": p.touchdown,
        "epa": p.epa,
        "success": p.success if p.success is not None else (p.yards_gained or 0) >= 4,
        "desc": p.desc,
    } for p in carries]

    # summarize by direction for the highlight layer
    zones: dict[tuple, dict] = {}
    for r in routes:
        key = (r["location"] or "unknown", r["depth_band"] or "short")
        z = zones.setdefault(key, {"location": key[0], "depth_band": key[1],
                                   "targets": 0, "catches": 0, "yards": 0.0, "successes": 0})
        z["targets"] += 1
        if r["complete"]:
            z["catches"] += 1
            z["yards"] += r["yards"] or 0
        if r["success"]:
            z["successes"] += 1

    return {
        "player": {"id": player_id, "name": player.name if player else player_id,
                   "position": player.position if player else None,
                   "team": player.team if player else None},
        "game": ({"id": game_id, "week": game.week, "home": game.home_team,
                  "away": game.away_team, "phase": game.phase} if game else {"id": game_id}),
        "tracking_note": "Derived from play-by-play outcomes (direction, depth, catch point), "
                         "not player-tracking data.",
        "routes": routes,
        "carries": runs,
        "zones": [
            {**z, "success_rate": round(z["successes"] / z["targets"], 2)}
            for z in zones.values()
        ],
    }
