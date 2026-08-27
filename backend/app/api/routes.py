"""REST endpoints. Everything reads from SQLite only — never a live API call."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.compare import compare_teams
from app.analytics.defense_profile import defense_profile
from app.analytics.lineup_impact import lineup_impact, roster_for_side
from app.analytics.player_quarters import player_quarter_splits
from app.analytics.route_charts import player_routes
from app.analytics.team_stats import season_team_totals, team_detail
from app.db.models import Game, SyncLog
from app.db.session import get_db

router = APIRouter(prefix="/api")


@router.get("/seasons")
def seasons(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Game.season, Game.phase, func.count(Game.id),
               func.sum(func.iif(Game.status != "scheduled", 1, 0)))
        .group_by(Game.season, Game.phase)
    ).all()
    out: dict[int, list] = {}
    for season, phase, n_games, n_played in rows:
        out.setdefault(season, []).append({
            "phase": phase, "games": n_games, "played": int(n_played or 0),
        })
    order = {"pre": 0, "reg": 1, "post": 2}
    return [
        {"season": season, "phases": sorted(phases, key=lambda p: order.get(p["phase"], 9))}
        for season, phases in sorted(out.items())
    ]


@router.get("/teams")
def teams(season: int = Query(...), phase: str = Query(...),
          db: Session = Depends(get_db)):
    return season_team_totals(db, season, phase)


@router.get("/teams/{team}")
def team(team: str, season: int, phase: str, db: Session = Depends(get_db)):
    return team_detail(db, team.upper(), season, phase)


@router.get("/teams/{team}/defense")
def team_defense(team: str, season: int, phase: str, db: Session = Depends(get_db)):
    return defense_profile(db, team.upper(), season, phase)


@router.get("/teams/{team}/roster")
def team_roster(team: str, side: str, season: int, phase: str,
                db: Session = Depends(get_db)):
    if side not in ("offense", "defense"):
        raise HTTPException(400, "side must be offense or defense")
    return roster_for_side(db, team.upper(), side, season, phase)


@router.get("/teams/{team}/lineup-impact")
def team_lineup_impact(team: str, side: str, players: str, season: int, phase: str,
                       db: Session = Depends(get_db)):
    if side not in ("offense", "defense"):
        raise HTTPException(400, "side must be offense or defense")
    ids = [p for p in players.split(",") if p]
    if not 1 <= len(ids) <= 6:
        raise HTTPException(400, "select between 1 and 6 players")
    return lineup_impact(db, team.upper(), side, ids, season, phase)


@router.get("/players/{player_id}/quarters")
def player_quarters(player_id: str, season: int, phase: str,
                    game: str | None = None, db: Session = Depends(get_db)):
    return player_quarter_splits(db, player_id, season, phase, game)


@router.get("/players/{player_id}/routes")
def player_route_chart(player_id: str, game: str, db: Session = Depends(get_db)):
    return player_routes(db, player_id, game)


@router.get("/compare")
def compare(teamA: str, teamB: str, season: int, phase: str,
            db: Session = Depends(get_db)):
    return compare_teams(db, teamA.upper(), teamB.upper(), season, phase)


@router.get("/live")
def live(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Game).where(Game.status == "in").order_by(Game.kickoff)
    ).all()
    return [{
        "game_id": g.id, "season": g.season, "phase": g.phase, "week": g.week,
        "home": g.home_team, "away": g.away_team,
        "home_score": g.home_score, "away_score": g.away_score,
        "kickoff": g.kickoff,
    } for g in rows]


@router.get("/status")
def status(db: Session = Depends(get_db)):
    recent = db.scalars(
        select(SyncLog).order_by(SyncLog.id.desc()).limit(25)
    ).all()
    last_ok = db.scalars(
        select(SyncLog).where(SyncLog.status == "ok")
        .order_by(SyncLog.id.desc()).limit(1)
    ).first()
    n_games = db.scalar(select(func.count(Game.id)))
    return {
        "games_in_db": n_games,
        "last_successful_sync": last_ok.created_at if last_ok else None,
        "recent": [{
            "at": r.created_at, "source": r.source, "scope": r.scope,
            "status": r.status, "rows": r.rows, "message": r.message,
        } for r in recent],
    }
