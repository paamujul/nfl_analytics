"""Team totals: passing / rushing / receiving yards, season and per game."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Game, Player, PlayerGameStat, Team


def _phase_game_ids(session: Session, season: int, phase: str) -> select:
    return select(Game.id).where(Game.season == season, Game.phase == phase)


def season_team_totals(session: Session, season: int, phase: str) -> list[dict]:
    games_q = _phase_game_ids(session, season, phase)
    agg = (
        select(
            PlayerGameStat.team,
            func.count(func.distinct(PlayerGameStat.game_id)).label("games"),
            func.sum(PlayerGameStat.pass_yds).label("pass_yds"),
            func.sum(PlayerGameStat.rush_yds).label("rush_yds"),
            func.sum(PlayerGameStat.rec_yds).label("rec_yds"),
            func.sum(PlayerGameStat.pass_td).label("pass_td"),
            func.sum(PlayerGameStat.rush_td).label("rush_td"),
            func.sum(PlayerGameStat.rec_td).label("rec_td"),
        )
        .where(PlayerGameStat.game_id.in_(games_q))
        .group_by(PlayerGameStat.team)
    )
    totals = {r.team: r for r in session.execute(agg)}
    teams = session.scalars(select(Team).order_by(Team.abbr)).all()
    out = []
    for t in teams:
        r = totals.get(t.abbr)
        out.append({
            "abbr": t.abbr, "name": t.name, "conference": t.conference,
            "division": t.division, "color": t.color, "color2": t.color2,
            "logo": t.logo,
            "games": r.games if r else 0,
            "pass_yds": int(r.pass_yds or 0) if r else 0,
            "rush_yds": int(r.rush_yds or 0) if r else 0,
            "rec_yds": int(r.rec_yds or 0) if r else 0,
            "pass_td": int(r.pass_td or 0) if r else 0,
            "rush_td": int(r.rush_td or 0) if r else 0,
            "rec_td": int(r.rec_td or 0) if r else 0,
        })
    return out


def team_detail(session: Session, team: str, season: int, phase: str) -> dict:
    t = session.get(Team, team)
    games = session.scalars(
        select(Game)
        .where(Game.season == season, Game.phase == phase)
        .where((Game.home_team == team) | (Game.away_team == team))
        .order_by(Game.kickoff)
    ).all()
    game_ids = [g.id for g in games]

    # per-game team totals
    per_game_rows = session.execute(
        select(
            PlayerGameStat.game_id,
            func.sum(PlayerGameStat.pass_yds), func.sum(PlayerGameStat.rush_yds),
            func.sum(PlayerGameStat.rec_yds),
        )
        .where(PlayerGameStat.game_id.in_(game_ids), PlayerGameStat.team == team)
        .group_by(PlayerGameStat.game_id)
    ).all()
    per_game = {gid: (p or 0, ru or 0, re or 0) for gid, p, ru, re in per_game_rows}

    game_list = []
    for g in games:
        p, ru, re = per_game.get(g.id, (0, 0, 0))
        opp = g.away_team if g.home_team == team else g.home_team
        game_list.append({
            "game_id": g.id, "week": g.week, "kickoff": g.kickoff,
            "opponent": opp, "home": g.home_team == team,
            "team_score": g.home_score if g.home_team == team else g.away_score,
            "opp_score": g.away_score if g.home_team == team else g.home_score,
            "status": g.status,
            "pass_yds": int(p), "rush_yds": int(ru), "rec_yds": int(re),
        })

    # player season aggregates for this team
    players_rows = session.execute(
        select(
            PlayerGameStat.player_id, Player.name, Player.position, Player.headshot,
            func.count(PlayerGameStat.game_id),
            func.sum(PlayerGameStat.pass_yds), func.sum(PlayerGameStat.pass_att),
            func.sum(PlayerGameStat.pass_td),
            func.sum(PlayerGameStat.rush_yds), func.sum(PlayerGameStat.rush_att),
            func.sum(PlayerGameStat.rush_td),
            func.sum(PlayerGameStat.rec_yds), func.sum(PlayerGameStat.receptions),
            func.sum(PlayerGameStat.targets), func.sum(PlayerGameStat.rec_td),
        )
        .join(Player, Player.id == PlayerGameStat.player_id)
        .where(PlayerGameStat.game_id.in_(game_ids), PlayerGameStat.team == team)
        .group_by(PlayerGameStat.player_id)
    ).all()

    players = [{
        "player_id": pid, "name": name, "position": pos, "headshot": hs,
        "games": g, "pass_yds": int(py or 0), "pass_att": int(pa or 0),
        "pass_td": int(ptd or 0), "rush_yds": int(ry or 0),
        "rush_att": int(ra or 0), "rush_td": int(rtd or 0),
        "rec_yds": int(recy or 0), "receptions": int(rec or 0),
        "targets": int(tg or 0), "rec_td": int(rectd or 0),
    } for pid, name, pos, hs, g, py, pa, ptd, ry, ra, rtd, recy, rec, tg, rectd in players_rows]
    players.sort(key=lambda p: p["pass_yds"] + p["rush_yds"] + p["rec_yds"], reverse=True)

    return {
        "team": {
            "abbr": team, "name": t.name if t else team,
            "color": t.color if t else None, "color2": t.color2 if t else None,
            "logo": t.logo if t else None,
            "conference": t.conference if t else None,
            "division": t.division if t else None,
        },
        "season": season, "phase": phase,
        "totals": {
            "games": len([g for g in game_list if g["status"] != "scheduled"]),
            "pass_yds": sum(g["pass_yds"] for g in game_list),
            "rush_yds": sum(g["rush_yds"] for g in game_list),
            "rec_yds": sum(g["rec_yds"] for g in game_list),
        },
        "games": game_list,
        "players": players,
    }
