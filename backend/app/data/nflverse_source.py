"""nflverse (nflreadpy) backfill: bulk-load a season into the SQLite store.

Used for the completed 2025 season now and for 2026 regular/post-season
weeks as nflverse publishes them (nightly during the season). Downloads are
cached as parquet under storage/cache by nflreadpy itself.
"""
from __future__ import annotations

import os

from app.config import CACHE_DIR, PHASE_FROM_NFLVERSE
from app.data.timeutil import nflverse_kickoff

os.environ.setdefault("NFLREADPY_CACHE_MODE", "filesystem")
os.environ.setdefault("NFLREADPY_CACHE_DIR", str(CACHE_DIR))

import nflreadpy  # noqa: E402  (env vars must be set before import)
import polars as pl  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db.models import Game, Play, Player, PlayerGameStat, SnapCount, SyncLog, Team  # noqa: E402
from app.db.session import db_session  # noqa: E402
from app.db.upsert import upsert_all  # noqa: E402


def _log(session, scope: str, status: str, message: str | None = None, rows: int | None = None):
    session.add(SyncLog(source="nflverse", scope=scope, status=status, message=message, rows=rows))


def _int(v):
    """Nullable int. nflverse ships nullable numerics as Float64 (parquet has no
    nullable int32), so `drive` arrives as 1.0 and must not be stored as a float."""
    return None if v is None else int(v)


def _bool(v):
    """Nullable bool from nflverse's 0.0/1.0 float flags.

    Deliberately NOT `bool(v or 0)`: these columns are genuinely nullable
    (qb_dropback is null on ~1.5k plays a season) and collapsing null to False
    would invent a charted "no" where there is no charting at all.
    """
    return None if v is None else bool(v)


def _str(v):
    """Nullable string, with nflverse's empty-string-as-missing normalised to None.

    `route`, `defense_man_zone_type` and `offense_formation` are never null in
    the participation file -- unavailable rows carry "" instead. Storing "" would
    make a `IS NOT NULL` filter report 100% coverage on a column that is really
    ~42% charted, so the empties are flattened here at the boundary.
    """
    if v is None:
        return None
    v = v.strip() if isinstance(v, str) else v
    return v or None


def sync_teams() -> int:
    df = nflreadpy.load_teams()
    rows = [{
        "abbr": r["team_abbr"], "name": r["team_name"],
        "conference": r.get("team_conf"), "division": r.get("team_division"),
        "color": r.get("team_color"), "color2": r.get("team_color2"),
        "logo": r.get("team_logo_espn"), "espn_id": str(r.get("team_id") or ""),
    } for r in df.to_dicts()]
    with db_session() as s:
        n = upsert_all(s, Team, rows)
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
            "kickoff": nflverse_kickoff(r.get("gameday"), r.get("gametime")),
            "source": "nflverse",
        })
    with db_session() as s:
        # don't clobber espn_event_id set by the live ingester
        for row in rows:
            row.pop("espn_event_id")
        n = upsert_all(s, Game, rows)
        _log(s, f"schedules {season}", "ok", rows=n)
    return n


_DEF_STATS = (
    "def_tackles_solo", "def_tackle_assists", "def_tackles_for_loss",
    "def_fumbles_forced", "def_sacks", "def_qb_hits", "def_interceptions",
    "def_pass_defended", "def_tds",
)
# Sacks and TFL are recorded in halves, so they must stay floats.
_FLOAT_DEF_STATS = frozenset({"def_sacks", "def_tackles_for_loss"})


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
            # Defensive box score: same download, previously discarded. Kept
            # nullable (see the model) -- `or 0` would be wrong for a source
            # that simply doesn't carry the column.
            **{c: (_int(r.get(c)) if c not in _FLOAT_DEF_STATS else r.get(c))
               for c in _DEF_STATS},
        })
    with db_session() as s:
        upsert_all(s, Player, list(players.values()))
        n = upsert_all(s, PlayerGameStat, stats)
        _log(s, f"player_stats {season}", "ok", rows=n)
    return n


_PBP_BOOL = ("complete_pass", "touchdown", "interception", "sack", "success")

# Extra load_pbp columns carrying play-call context. Selected, not parsed: every
# one of these is a typed field, which is why nothing here reads `desc` prose.
_PBP_EXTRA = (
    "shotgun", "no_huddle", "qb_dropback", "qb_scramble",
    "drive", "fixed_drive", "fixed_drive_result", "drive_start_yard_line",
    "drive_play_count", "series", "series_result",
    "score_differential", "game_seconds_remaining", "half_seconds_remaining",
    "wp", "xpass", "pass_oe", "goal_to_go", "first_down",
    "posteam_timeouts_remaining", "pass_length", "penalty",
)
_PBP_EXTRA_BOOL = ("shotgun", "no_huddle", "qb_dropback", "qb_scramble",
                   "goal_to_go", "first_down", "penalty")
_PBP_EXTRA_INT = ("drive", "fixed_drive", "drive_play_count", "series",
                  "posteam_timeouts_remaining")
_PBP_EXTRA_STR = ("fixed_drive_result", "drive_start_yard_line", "series_result",
                  "pass_length")

# load_participation columns beyond the three we already kept.
_PART_EXTRA = (
    "offense_formation", "offense_personnel", "defense_personnel",
    "defenders_in_box", "was_pressure", "defense_man_zone_type",
    "defense_coverage_type", "route", "time_to_throw", "n_offense", "n_defense",
)
_PART_STR = ("offense_formation", "offense_personnel", "defense_personnel",
             "defense_man_zone_type", "defense_coverage_type", "route")
_PART_INT = ("defenders_in_box", "n_offense", "n_defense")

# load_ftn_charting columns beyond n_pass_rushers/n_blitzers.
_FTN_EXTRA = (
    "is_play_action", "is_motion", "is_screen_pass", "is_rpo", "is_trick_play",
    "is_qb_sneak", "qb_location", "n_offense_backfield", "n_defense_box",
)
_FTN_BOOL = ("is_play_action", "is_motion", "is_screen_pass", "is_rpo",
             "is_trick_play", "is_qb_sneak")
_FTN_INT = ("n_offense_backfield", "n_defense_box")


def sync_pbp(season: int) -> int:
    """Play-by-play + participation + FTN charting -> plays table.

    Everything here is a column selection from files the app already downloads.

    Coverage is uneven and the new columns are all nullable with no default;
    a consumer that reads None as 0/False will be wrong. Measured fill:

      offense/defense_personnel   76% (2021-22)   100% (2023+)
      offense_formation           ~73-80% throughout
      was_pressure, route         participation-wide, but `route` is only
                                  ~42% genuinely charted -- the rest is ""
                                  (normalised to NULL by _str)
      defense_coverage_type       ~50%
      FTN (is_play_action etc.)   2022+ only; load_ftn_charting raises
                                  ValueError for 2021

    Participation and FTN are denormalised into `plays` rather than split into
    side tables: they are always read alongside the play, and a join across
    ~240k rows costs more than the extra row width.
    """
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
        # nflreadpy raises ValueError("Season must be between 2022 and 2025")
        # for 2021 -- FTN charting simply does not exist that far back. Verified,
        # not assumed: the whole season must still load without it.
        pass

    cols = [c for c in (
        "game_id", "play_id", "qtr", "time", "down", "ydstogo", "yardline_100",
        "posteam", "defteam", "play_type", "yards_gained", "air_yards",
        "yards_after_catch", "pass_location", "run_location", "run_gap",
        "passer_player_id", "rusher_player_id", "receiver_player_id",
        "complete_pass", "touchdown", "interception", "sack", "epa", "success",
        "desc", *_PBP_EXTRA,
    ) if c in pbp.columns]
    df = pbp.select(cols)

    if part is not None and len(part) > 0:
        pcols = set(part.columns)
        gid = "nflverse_game_id" if "nflverse_game_id" in pcols else "game_id"
        pr_col = next((c for c in ("number_of_pass_rushers", "n_pass_rushers") if c in pcols), None)
        keep = [gid, "play_id"]
        keep += [c for c in ("offense_players", "defense_players", *_PART_EXTRA) if c in pcols]
        if pr_col:
            keep.append(pr_col)
        pj = part.select(keep).rename({gid: "game_id"})
        if pr_col:
            pj = pj.rename({pr_col: "part_pass_rushers"})
        # participation's play_id is Float64 while pbp's may already be Int64.
        pj = pj.with_columns(pl.col("play_id").cast(pl.Int64))
        df = df.with_columns(pl.col("play_id").cast(pl.Int64)).join(
            pj, on=["game_id", "play_id"], how="left")

    if ftn is not None and len(ftn) > 0:
        # The join needs both key columns and at least one payload column. The
        # old guard was `len(keep) >= 3`, which happened to mean the same thing
        # for a 4-column list but silently drops the entire join now that the
        # list is 11 long -- one absent optional column would have cost us all
        # of FTN. Test the keys explicitly instead.
        keys = ("nflverse_game_id", "nflverse_play_id")
        payload = [c for c in ("n_pass_rushers", "n_blitzers", *_FTN_EXTRA) if c in ftn.columns]
        if all(k in ftn.columns for k in keys) and payload:
            fj = ftn.select([*keys, *payload]).rename(
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
            # play-call context
            **{k: _bool(r.get(k)) for k in _PBP_EXTRA_BOOL},
            **{k: _int(r.get(k)) for k in _PBP_EXTRA_INT},
            **{k: _str(r.get(k)) for k in _PBP_EXTRA_STR},
            **{k: r.get(k) for k in ("score_differential", "game_seconds_remaining",
                                     "half_seconds_remaining", "wp", "xpass", "pass_oe")},
            # participation
            **{k: _str(r.get(k)) for k in _PART_STR},
            **{k: _int(r.get(k)) for k in _PART_INT},
            "was_pressure": _bool(r.get("was_pressure")),
            "time_to_throw": r.get("time_to_throw"),
            # FTN
            **{k: _bool(r.get(k)) for k in _FTN_BOOL},
            **{k: _int(r.get(k)) for k in _FTN_INT},
            "qb_location": _str(r.get("qb_location")),
        })

    game_ids = {r["game_id"] for r in rows}
    with db_session() as s:
        # bulk replace the season's plays: simpler than row-diffing, idempotent
        s.execute(delete(Play).where(Play.game_id.in_(game_ids)))
        n = upsert_all(s, Play, rows)
        has_part = part is not None and len(part) > 0
        has_ftn = ftn is not None and len(ftn) > 0
        _log(s, f"pbp {season}", "ok",
             message=(f"participation={'yes' if has_part else 'no'} "
                      f"ftn={'yes' if has_ftn else 'no'}"), rows=n)
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
        n = upsert_all(s, SnapCount, rows)
        _log(s, f"snap_counts {season}", "ok", rows=n)
    return n


def backfill_season(season: int) -> dict[str, int]:
    """Full nflverse backfill for one season.

    Most datasets are independent: early in a season nflverse publishes some
    files before others (and none before week 1), so one missing release must
    not abort the rest. Schedules is the exception -- plays and player stats
    carry foreign keys onto games, so writing them after a failed schedules
    step produces orphan rows. SQLite never enforced those FKs; Postgres does.

    Returns per-step row counts plus a "failed" count for callers that need an
    exit status.
    """
    steps = [
        ("teams", sync_teams, ()),
        ("schedules", lambda: sync_schedules(season), ()),
        ("player_stats", lambda: sync_players_and_stats(season), ("schedules",)),
        ("plays", lambda: sync_pbp(season), ("schedules",)),
        ("snap_counts", lambda: sync_snap_counts(season), ()),
    ]
    out: dict[str, int] = {}
    failed: set[str] = set()
    for name, fn, requires in steps:
        blockers = sorted(failed.intersection(requires))
        if blockers:
            out[name] = 0
            failed.add(name)
            with db_session() as s:
                _log(s, f"{name} {season}", "error",
                     message=f"skipped: {', '.join(blockers)} failed")
            continue
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = 0
            failed.add(name)
            with db_session() as s:
                _log(s, f"{name} {season}", "error", message=str(e)[:300])
    out["failed"] = len(failed)
    return out
