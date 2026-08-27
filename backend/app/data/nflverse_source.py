"""nflverse (nflreadpy) backfill: bulk-load a season into the SQLite store.

Used for the completed 2025 season now and for 2026 regular/post-season
weeks as nflverse publishes them (nightly during the season). Downloads are
cached as parquet under storage/cache by nflreadpy itself.
"""
from __future__ import annotations

import os

from app.config import CACHE_DIR, PHASE_FROM_NFLVERSE

os.environ.setdefault("NFLREADPY_CACHE_MODE", "filesystem")
os.environ.setdefault("NFLREADPY_CACHE_DIR", str(CACHE_DIR))

import nflreadpy  # noqa: E402  (env vars must be set before import)
import polars as pl  # noqa: E402
from sqlalchemy import delete  # noqa: E402
from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402

from app.db.models import Game, Play, Player, PlayerGameStat, SnapCount, SyncLog, Team  # noqa: E402
from app.db.session import db_session  # noqa: E402


def _upsert_all(session, model, rows: list[dict], chunk: int = 2000) -> int:
    if not rows:
        return 0
    pk_cols = [c.name for c in model.__table__.primary_key.columns]
    # multi-row inserts take their column list from the first row, so pad
    # every row to the full column set (using model defaults where declared)
    cols = list(model.__table__.columns)
    defaults = {c.name: c.default.arg for c in cols
                if c.default is not None and c.default.is_scalar}
    rows = [{c.name: r.get(c.name, defaults.get(c.name)) for c in cols} for r in rows]
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        stmt = sqlite_insert(model.__table__).values(batch)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in model.__table__.columns if c.name not in pk_cols
        }
        stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=update_cols)
        session.execute(stmt)
    return len(rows)


def _log(session, scope: str, status: str, message: str | None = None, rows: int | None = None):
    session.add(SyncLog(source="nflverse", scope=scope, status=status, message=message, rows=rows))


def sync_teams() -> int:
    df = nflreadpy.load_teams()
    rows = [{
        "abbr": r["team_abbr"], "name": r["team_name"],
        "conference": r.get("team_conf"), "division": r.get("team_division"),
        "color": r.get("team_color"), "color2": r.get("team_color2"),
        "logo": r.get("team_logo_espn"), "espn_id": str(r.get("team_id") or ""),
    } for r in df.to_dicts()]
    with db_session() as s:
        n = _upsert_all(s, Team, rows)
        _log(s, "teams", "ok", rows=n)
    return n


def sync_schedules(season: int) -> int:
    df = nflreadpy.load_schedules([season])
    rows = []
    for r in df.to_dicts():
        phase = PHASE_FROM_NFLVERSE.get(r["game_type"])
        if not phase:
            continue
        finished = r.get("home_score") is not None
        rows.append({
            "id": r["game_id"], "espn_event_id": None,
            "season": r["season"], "phase": phase, "week": r["week"],
            "home_team": r["home_team"], "away_team": r["away_team"],
            "home_score": r.get("home_score"), "away_score": r.get("away_score"),
            "status": "final" if finished else "scheduled",
            "kickoff": f'{r.get("gameday", "")}T{r.get("gametime") or "00:00"}',
            "source": "nflverse",
        })
    with db_session() as s:
        # don't clobber espn_event_id set by the live ingester
        for row in rows:
            row.pop("espn_event_id")
        n = _upsert_all(s, Game, rows)
        _log(s, f"schedules {season}", "ok", rows=n)
    return n


def sync_players_and_stats(season: int) -> int:
    df = nflreadpy.load_player_stats([season])
    players: dict[str, dict] = {}
    stats: list[dict] = []
    for r in df.to_dicts():
        pid = r["player_id"]
        if not pid:
            continue
        players[pid] = {
            "id": pid, "espn_id": None,
            "name": r.get("player_display_name") or r.get("player_name") or pid,
            "position": r.get("position"), "team": r.get("team"),
            "headshot": r.get("headshot_url"),
        }
        stats.append({
            "game_id": r["game_id"], "player_id": pid, "team": r.get("team"),
            "pass_att": int(r.get("attempts") or 0),
            "pass_cmp": int(r.get("completions") or 0),
            "pass_yds": int(r.get("passing_yards") or 0),
            "pass_td": int(r.get("passing_tds") or 0),
            "pass_int": int(r.get("passing_interceptions") or 0),
            "sacks_taken": int(r.get("sacks_suffered") or 0),
            "rush_att": int(r.get("carries") or 0),
            "rush_yds": int(r.get("rushing_yards") or 0),
            "rush_td": int(r.get("rushing_tds") or 0),
            "targets": int(r.get("targets") or 0),
            "receptions": int(r.get("receptions") or 0),
            "rec_yds": int(r.get("receiving_yards") or 0),
            "rec_td": int(r.get("receiving_tds") or 0),
        })
    with db_session() as s:
        _upsert_all(s, Player, list(players.values()))
        n = _upsert_all(s, PlayerGameStat, stats)
        _log(s, f"player_stats {season}", "ok", rows=n)
    return n


_PBP_BOOL = ("complete_pass", "touchdown", "interception", "sack", "success")


def sync_pbp(season: int) -> int:
    """Play-by-play + participation + FTN charting -> plays table."""
    pbp = nflreadpy.load_pbp([season])

    part = None
    try:
        part = nflreadpy.load_participation([season])
    except Exception:
        pass  # participation publishes after the post-season; fine to miss
    ftn = None
    try:
        ftn = nflreadpy.load_ftn_charting([season])
    except Exception:
        pass

    cols = [c for c in (
        "game_id", "play_id", "qtr", "time", "down", "ydstogo", "yardline_100",
        "posteam", "defteam", "play_type", "yards_gained", "air_yards",
        "yards_after_catch", "pass_location", "run_location", "run_gap",
        "passer_player_id", "rusher_player_id", "receiver_player_id",
        "complete_pass", "touchdown", "interception", "sack", "epa", "success",
        "desc",
    ) if c in pbp.columns]
    df = pbp.select(cols)

    if part is not None and len(part) > 0:
        pcols = {c: c for c in part.columns}
        gid = "nflverse_game_id" if "nflverse_game_id" in pcols else "game_id"
        pr_col = next((c for c in ("number_of_pass_rushers", "n_pass_rushers") if c in pcols), None)
        keep = [gid, "play_id", "offense_players", "defense_players"] + ([pr_col] if pr_col else [])
        pj = part.select(keep).rename({gid: "game_id"})
        if pr_col:
            pj = pj.rename({pr_col: "part_pass_rushers"})
        df = df.join(pj, on=["game_id", "play_id"], how="left")

    if ftn is not None and len(ftn) > 0:
        keep = [c for c in ("nflverse_game_id", "nflverse_play_id", "n_pass_rushers", "n_blitzers")
                if c in ftn.columns]
        if len(keep) >= 3:
            fj = ftn.select(keep).rename(
                {"nflverse_game_id": "game_id", "nflverse_play_id": "play_id"}
            ).with_columns(pl.col("play_id").cast(pl.Int64))
            df = df.with_columns(pl.col("play_id").cast(pl.Int64)).join(
                fj, on=["game_id", "play_id"], how="left", suffix="_ftn")

    rows: list[dict] = []
    for r in df.to_dicts():
        n_rush = r.get("n_pass_rushers") or r.get("part_pass_rushers")
        n_blitz = r.get("n_blitzers")
        rows.append({
            "game_id": r["game_id"], "play_id": int(r["play_id"]),
            "quarter": r.get("qtr"), "clock": r.get("time"),
            "down": r.get("down"), "ydstogo": r.get("ydstogo"),
            "yardline_100": r.get("yardline_100"),
            "posteam": r.get("posteam"), "defteam": r.get("defteam"),
            "play_type": r.get("play_type"), "yards_gained": r.get("yards_gained"),
            "air_yards": r.get("air_yards"), "yac": r.get("yards_after_catch"),
            "pass_location": r.get("pass_location"),
            "pass_depth": (None if r.get("air_yards") is None
                           else ("deep" if r["air_yards"] >= 15 else "short")),
            "run_location": r.get("run_location"), "run_gap": r.get("run_gap"),
            "passer_id": r.get("passer_player_id"),
            "rusher_id": r.get("rusher_player_id"),
            "receiver_id": r.get("receiver_player_id"),
            **{k: (None if r.get(k) is None else bool(r[k])) for k in _PBP_BOOL},
            "touchdown": bool(r.get("touchdown")),
            "interception": bool(r.get("interception")),
            "sack": bool(r.get("sack")),
            "epa": r.get("epa"),
            "offense_players": r.get("offense_players"),
            "defense_players": r.get("defense_players"),
            "n_pass_rushers": (int(n_rush) if n_rush is not None else None),
            "is_blitz": (None if n_blitz is None else bool(n_blitz and n_blitz > 0)),
            "desc": r.get("desc"),
        })

    game_ids = {r["game_id"] for r in rows}
    with db_session() as s:
        # bulk replace the season's plays: simpler than row-diffing, idempotent
        s.execute(delete(Play).where(Play.game_id.in_(game_ids)))
        n = _upsert_all(s, Play, rows, chunk=1000)
        has_part = part is not None and len(part) > 0
        _log(s, f"pbp {season}", "ok",
             message=f"participation={'yes' if has_part else 'no'}", rows=n)
    return n


def sync_snap_counts(season: int) -> int:
    try:
        df = nflreadpy.load_snap_counts([season])
    except Exception as e:
        with db_session() as s:
            _log(s, f"snap_counts {season}", "error", message=str(e))
        return 0
    rows = [{
        "game_id": r["game_id"], "player_name": r["player"], "team": r["team"],
        "season": r["season"], "week": r["week"], "position": r.get("position"),
        "opponent": r.get("opponent"),
        "offense_snaps": int(r.get("offense_snaps") or 0),
        "offense_pct": float(r.get("offense_pct") or 0),
        "defense_snaps": int(r.get("defense_snaps") or 0),
        "defense_pct": float(r.get("defense_pct") or 0),
    } for r in df.to_dicts()]
    with db_session() as s:
        n = _upsert_all(s, SnapCount, rows)
        _log(s, f"snap_counts {season}", "ok", rows=n)
    return n


def backfill_season(season: int) -> dict[str, int]:
    """Full nflverse backfill for one season."""
    out = {"teams": sync_teams(), "schedules": sync_schedules(season)}
    out["player_stats"] = sync_players_and_stats(season)
    out["plays"] = sync_pbp(season)
    out["snap_counts"] = sync_snap_counts(season)
    return out
