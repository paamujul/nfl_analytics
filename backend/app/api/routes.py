"""REST endpoints. Everything reads from the database only — never a live API call."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.analytics.compare import compare_teams
from app.analytics.defense_personnel import team_defense_personnel
from app.analytics.defense_profile import defense_profile
from app.analytics.drives import SCRIPT_LENGTH, team_drives
from app.analytics.lineup_impact import lineup_impact, roster_for_side
from app.analytics.playbook import play_caller_playbook, team_playbook
from app.analytics.player_quarters import player_quarter_splits
from app.analytics.route_charts import player_routes
from app.analytics.team_stats import season_team_totals, team_detail
from app.analytics.usage import team_usage
from app.data.timeutil import parse_iso
from app.db.models import Game, SyncLog
from app.db.session import get_db

# Browser/edge TTL for the analytics endpoints. Matches the TTL on the
# in-process league cache (app/cache.py), so a client and the process behind it
# never disagree about how stale these numbers are allowed to be.
CACHE_SECONDS = 300

# how stale last_successful_sync may get before /health reports unhealthy
HEALTH_MAX_SYNC_AGE = timedelta(hours=6)

router = APIRouter(prefix="/api")


@router.get("/seasons")
def seasons(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Game.season, Game.phase, func.count(Game.id),
               func.sum(case((Game.status != "scheduled", 1), else_=0)))
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
def teams(response: Response, season: int = Query(...), phase: str = Query(...),
          db: Session = Depends(get_db)):
    # Already a cheap SQL aggregate, so no in-process cache here -- this header
    # exists only to let the edge absorb the repeats a grid of team cards makes.
    response.headers["Cache-Control"] = "public, max-age=60"
    return season_team_totals(db, season, phase)


@router.get("/teams/{team}")
def team(team: str, response: Response, season: int, phase: str,
         db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "public, max-age=60"
    return team_detail(db, team.upper(), season, phase)


@router.get("/teams/{team}/defense")
def team_defense(team: str, response: Response, season: int, phase: str,
                 db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return defense_profile(db, team.upper(), season, phase)


@router.get("/teams/{team}/roster")
def team_roster(team: str, side: str, season: int, phase: str,
                db: Session = Depends(get_db)):
    if side not in ("offense", "defense"):
        raise HTTPException(400, "side must be offense or defense")
    return roster_for_side(db, team.upper(), side, season, phase)


@router.get("/teams/{team}/lineup-impact")
def team_lineup_impact(team: str, response: Response, side: str, players: str,
                       season: int, phase: str, db: Session = Depends(get_db)):
    if side not in ("offense", "defense"):
        raise HTTPException(400, "side must be offense or defense")
    ids = [p for p in players.split(",") if p]
    if not 1 <= len(ids) <= 6:
        raise HTTPException(400, "select between 1 and 6 players")
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return lineup_impact(db, team.upper(), side, ids, season, phase)


@router.get("/players/{player_id}/quarters")
def player_quarters(player_id: str, season: int, phase: str,
                    game: str | None = None, db: Session = Depends(get_db)):
    return player_quarter_splits(db, player_id, season, phase, game)


@router.get("/players/{player_id}/routes")
def player_route_chart(player_id: str, game: str, db: Session = Depends(get_db)):
    return player_routes(db, player_id, game)


@router.get("/compare")
def compare(response: Response, teamA: str, teamB: str, season: int, phase: str,
            db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return compare_teams(db, teamA.upper(), teamB.upper(), season, phase)


# --- coaching views -------------------------------------------------------
# All four are backed by a league-wide reduction held in app/cache.py for
# CACHE_SECONDS, so the edge TTL matches: a client and the process behind it
# should never disagree about how stale these numbers are allowed to be.
#
# `response: Response` has no default, so it must precede the Depends()
# parameters -- Python would reject the signature otherwise.


@router.get("/coach/playbook")
def coach_playbook(response: Response, season: int, phase: str,
                   team: str | None = None, coach: str | None = None,
                   db: Session = Depends(get_db)):
    """Offensive playbook for one team, or for one play-caller across teams."""
    if bool(team) == bool(coach):
        raise HTTPException(400, "pass exactly one of team or coach")
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    if team:
        return team_playbook(db, team.upper(), season, phase)
    try:
        return play_caller_playbook(db, coach, season, phase)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/coach/drives")
def coach_drives(response: Response, team: str, season: int, phase: str,
                 script_length: int = SCRIPT_LENGTH,
                 db: Session = Depends(get_db)):
    if not 1 <= script_length <= 40:
        raise HTTPException(400, "script_length must be between 1 and 40")
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return team_drives(db, team.upper(), season, phase, script_length)


@router.get("/coach/usage")
def coach_usage(response: Response, team: str, season: int, phase: str,
                db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return team_usage(db, team.upper(), season, phase)


@router.get("/coach/defense")
def coach_defense(response: Response, team: str, season: int, phase: str,
                  db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return team_defense_personnel(db, team.upper(), season, phase)


@router.get("/live")
def live(response: Response, db: Session = Depends(get_db)):
    # In-progress scores: a stale copy is worse than a slow one.
    response.headers["Cache-Control"] = "no-store"
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
def status(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
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


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Liveness + freshness, for uptime monitoring.

    A plain 200 only proves the web process is up. The scheduled ingester can
    wedge while the API stays perfectly healthy, so report 503 once the last
    successful sync goes stale -- that is the failure worth being paged for.
    """
    last_ok = db.scalar(
        select(SyncLog.created_at).where(SyncLog.status == "ok")
        .order_by(SyncLog.id.desc()).limit(1)
    )
    age = None
    if last_ok:
        synced = parse_iso(last_ok)
        if synced:
            age = (datetime.now(timezone.utc) - synced).total_seconds()
    ok = age is not None and age <= HEALTH_MAX_SYNC_AGE.total_seconds()
    body = {"status": "ok" if ok else "stale",
            "last_successful_sync": last_ok,
            "age_seconds": int(age) if age is not None else None}
    # Set on the response we actually return, not on an injected Response --
    # FastAPI only merges that one's headers into a value it serializes itself,
    # and this route hands back its own. A cached 200 here would mask a wedged
    # ingester from exactly the 503 UptimeRobot is watching for.
    return JSONResponse(body, status_code=200 if ok else 503,
                        headers={"Cache-Control": "no-store"})
