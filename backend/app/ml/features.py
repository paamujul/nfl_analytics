"""Feature engineering for the play-call model.

Everything here is polars (already a dependency, used by the nflverse loader) --
no pandas. LightGBM is not imported in this module at all, so `features` can be
exercised by tests and by any future API surface without pulling the trainer in.

WHAT IS AND IS NOT AN INPUT
---------------------------
The target is P(pass) on a scrimmage snap, and the point of comparison is
nflverse's `xpass`, which sees game state only. Three columns that look like
obvious features are actually the label wearing a hat, and are excluded:

  * `passer_id` -- null on 76,438 of 76,438 runs and populated on 104,113 of
    104,137 passes in the 2021-25 backfill. Using it yields AUC ~1.0 and a model
    that has learned nothing. The QB *identity* is still wanted, so we derive
    `qb_id` = the team's modal passer across the whole game, which is knowable
    before the snap and carries no per-play signal.
  * `is_screen_pass` -- 0.093 on passes, exactly 0.000 on runs. One-way perfect
    indicator.
  * `is_play_action` (0.217 vs 0.020) and `is_rpo` (0.022 vs 0.087) -- FTN
    charts these off the play that was run, so they describe the call rather
    than precede it.

`shotgun`, `no_huddle`, `offense_formation`, `offense_personnel` and `is_motion`
are pre-snap *alignment*: the defense can see them, but they are chosen with the
play, and `xpass` does not get them. Feeding them to a model whose headline
claim is "beats xpass" would make the comparison meaningless, so they live in a
second, separately reported feature set:

    FEATURES_SITUATION  game state + identity + sequence   <- the xpass-comparable one
    FEATURES_PRESNAP    the above + alignment              <- strictly more informed

`train.py` fits both and reports both, and only the situational number is
allowed to satisfy the gate.

NULLS ARE NOT IMPUTED. LightGBM routes missing values down their own branch;
inventing a 2021 motion rate that was never charted would be worse than the gap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl

# --------------------------------------------------------------------------
# personnel parsing
# --------------------------------------------------------------------------
# The analytics package owns the canonical parser. Import it when it exists so
# the two agents' work converges; fall back to the local implementation below
# otherwise. Both must agree that RB counts include FB -- see _parse_personnel.
try:  # pragma: no cover - depends on sibling module landing
    from app.analytics.personnel import parse_personnel as _parse_personnel_ext
except Exception:  # pragma: no cover
    _parse_personnel_ext = None

_POS_TOKEN = re.compile(r"(\d+)\s*([A-Za-z]+)")


def parse_personnel(text: str | None) -> tuple[int | None, int | None, int | None]:
    """`offense_personnel` -> (n_rb, n_te, n_wr), or (None, None, None).

    The column carries TWO different formats and the backfill mixes them by
    season, which is the thing that breaks a naive positional split:

        2021-22 (nflverse participation, 35,625/35,766 and 35,341/35,430 rows)
            "1 RB, 1 TE, 3 WR"          -- skill positions only, FB folded into RB
        2023-25 (FTN, 35,572/35,600 and up)
            "1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 3 WR"   -- every position, FB split out

    So we count labelled tokens rather than reading positions off by index, and
    add FB into RB, which makes the FTN seasons agree with the nflverse ones and
    reproduces the familiar 11/12/21 shorthand.
    """
    if _parse_personnel_ext is not None:  # pragma: no cover
        return _parse_personnel_ext(text)
    if not text:
        return (None, None, None)
    counts: dict[str, int] = {}
    for n, pos in _POS_TOKEN.findall(text):
        counts[pos.upper()] = counts.get(pos.upper(), 0) + int(n)
    if not counts:
        return (None, None, None)
    return (counts.get("RB", 0) + counts.get("FB", 0),
            counts.get("TE", 0),
            counts.get("WR", 0))


# --------------------------------------------------------------------------
# feature sets
# --------------------------------------------------------------------------
SITUATION = [
    "down", "ydstogo", "yardline_100", "score_differential",
    "game_seconds_remaining", "half_seconds_remaining", "quarter",
    "posteam_timeouts_remaining", "wp", "goal_to_go",
]

# nflverse's own model, both as an input and as the thing to beat.
BASELINE = ["xpass"]

IDENTITY = ["play_caller_id", "posteam", "defteam", "qb_id"]

SEQUENCE = [
    "pass_rate_last_5", "pass_rate_last_10", "epa_mean_last_10",
    "pass_rate_game", "pass_success_rate_game", "run_success_rate_game",
    "plays_so_far_game", "plays_this_drive", "drive_number",
    "prev_is_pass", "prev_success", "prev_epa",
]

ALIGNMENT = ["offense_formation", "personnel_group", "n_rb", "n_te", "n_wr",
             "shotgun", "no_huddle", "is_motion"]

FEATURES_SITUATION = SITUATION + BASELINE + IDENTITY + SEQUENCE
FEATURES_PRESNAP = FEATURES_SITUATION + ALIGNMENT

CATEGORICAL = {"play_caller_id", "posteam", "defteam", "qb_id",
               "offense_formation", "personnel_group"}

FEATURE_SETS = {"situation": FEATURES_SITUATION, "presnap": FEATURES_PRESNAP}

# Columns pulled straight out of `plays`/`games`. Kept explicit because
# SELECT * on a 70-column table across 176k rows is ~4x the memory for nothing.
_PLAY_COLUMNS = """
    g.season, g.week, g.phase, p.game_id, p.play_id, p.posteam, p.defteam,
    p.play_type, p.down, p.ydstogo, p.yardline_100,
    CAST(p.score_differential AS REAL)      AS score_differential,
    CAST(p.game_seconds_remaining AS REAL)  AS game_seconds_remaining,
    CAST(p.half_seconds_remaining AS REAL)  AS half_seconds_remaining,
    p.quarter, p.posteam_timeouts_remaining,
    CAST(p.wp AS REAL)   AS wp,
    CAST(p.xpass AS REAL) AS xpass,
    CAST(p.epa AS REAL)  AS epa,
    p.goal_to_go, p.success, p.passer_id, p.fixed_drive,
    p.offense_personnel, p.offense_formation, p.shotgun, p.no_huddle, p.is_motion
"""

# SQLite stores BOOLEAN as INTEGER and FLOAT columns hold ints wherever the
# value was whole, so polars' row-wise inference hits mixed i64/f64 in the same
# column and raises ComputeError. Pin every column instead of guessing.
_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32, "week": pl.Int32, "play_id": pl.Int64,
    "down": pl.Int32, "ydstogo": pl.Int32, "yardline_100": pl.Int32,
    "quarter": pl.Int32, "posteam_timeouts_remaining": pl.Int32,
    "fixed_drive": pl.Int32,
    "score_differential": pl.Float64, "game_seconds_remaining": pl.Float64,
    "half_seconds_remaining": pl.Float64, "wp": pl.Float64,
    "xpass": pl.Float64, "epa": pl.Float64,
    "goal_to_go": pl.Int32, "success": pl.Int32,
    "shotgun": pl.Int32, "no_huddle": pl.Int32, "is_motion": pl.Int32,
}


@dataclass(frozen=True)
class PlayFrames:
    """Raw inputs, kept separate so tests can build them without a database."""
    plays: pl.DataFrame
    staff: pl.DataFrame   # season, team, offensive_play_caller_id


def load_play_frames(engine, seasons: tuple[int, ...]) -> PlayFrames:
    """Read scrimmage plays and coaching staff for `seasons`.

    Scrimmage only: play_type in ('pass','run'). The other 70k rows in the
    backfill are kickoffs, punts, kneels, spikes and no_plays, none of which are
    a pass/run decision.
    """
    lo, hi = min(seasons), max(seasons)
    plays_sql = f"""
        SELECT {_PLAY_COLUMNS}
        FROM plays p JOIN games g ON g.id = p.game_id
        WHERE p.play_type IN ('pass', 'run')
          AND g.season BETWEEN {int(lo)} AND {int(hi)}
    """
    staff_sql = f"""
        SELECT season, team, offensive_play_caller_id
        FROM coaching_staff
        WHERE season BETWEEN {int(lo)} AND {int(hi)}
    """
    with engine.connect() as conn:
        plays = pl.read_database(plays_sql, connection=conn,
                                 schema_overrides=_SCHEMA, infer_schema_length=None)
        staff = pl.read_database(staff_sql, connection=conn,
                                 schema_overrides={"season": pl.Int32},
                                 infer_schema_length=None)
    plays = plays.filter(pl.col("season").is_in(list(seasons)))
    return PlayFrames(plays=plays, staff=staff)


def build_features(frames: PlayFrames) -> pl.DataFrame:
    """Play frame -> model matrix, one row per scrimmage snap.

    Adds `is_pass` (the label), `weight_key` columns (season/posteam) that
    continuity.py joins on, and every column named in FEATURES_PRESNAP.
    """
    df = frames.plays

    # Ordering is load-bearing: every rolling window below is a plain `.over()`
    # on a pre-sorted frame. play_id is monotonic within a game in nflverse
    # data, so it is the sequence key -- clock is not (it stops).
    df = df.sort(["game_id", "posteam", "play_id"])

    df = df.with_columns(
        is_pass=(pl.col("play_type") == "pass").cast(pl.Int8),
        success_f=pl.col("success").cast(pl.Float64),
    )

    df = _add_identity(df, frames.staff)
    df = _add_alignment(df)
    df = _add_sequence(df)

    for col in CATEGORICAL:
        df = df.with_columns(pl.col(col).cast(pl.Categorical))
    return df


def _add_identity(df: pl.DataFrame, staff: pl.DataFrame) -> pl.DataFrame:
    """Play-caller and QB identity.

    play_caller_id comes from coaching_staff, which is 160/160 filled for
    2021-25 -- but every row has verified=0. It is derived, not sourced: the
    coordinator list is model-generated, and nflverse's per-game coach feed
    degrades after 2023 (2024-25 record one coach per team-season, so an
    in-season change like NYJ 2024 Saleh->Ulbrich is attributed entirely to
    Saleh across all 17 games). Treat this feature as noisy in a known
    direction, and say so anywhere a prediction is surfaced.

    qb_id is the modal passer over the team's whole game -- see the module
    docstring on why the per-play passer_id cannot be used.
    """
    staff = staff.rename({"team": "posteam",
                          "offensive_play_caller_id": "play_caller_id"})
    df = df.join(staff, on=["season", "posteam"], how="left")

    qb = (df.filter(pl.col("passer_id").is_not_null())
            .group_by(["game_id", "posteam", "passer_id"])
            .len()
            .sort(["game_id", "posteam", "len"], descending=[False, False, True])
            .group_by(["game_id", "posteam"], maintain_order=True)
            .first()
            .select(["game_id", "posteam", pl.col("passer_id").alias("qb_id")]))
    return df.join(qb, on=["game_id", "posteam"], how="left")


def _add_alignment(df: pl.DataFrame) -> pl.DataFrame:
    parsed = [parse_personnel(t) for t in df["offense_personnel"].to_list()]
    df = df.with_columns(
        n_rb=pl.Series([p[0] for p in parsed], dtype=pl.Int32),
        n_te=pl.Series([p[1] for p in parsed], dtype=pl.Int32),
        n_wr=pl.Series([p[2] for p in parsed], dtype=pl.Int32),
    )
    return df.with_columns(
        personnel_group=pl.when(pl.col("n_rb").is_null())
        .then(None)
        .otherwise(pl.col("n_rb").cast(pl.String) + pl.col("n_te").cast(pl.String))
    )


def _add_sequence(df: pl.DataFrame) -> pl.DataFrame:
    """In-game rolling features -- the part that makes predictions move as a
    game unfolds.

    Every window is `.shift(1)` FIRST and rolled SECOND, so a play never
    contributes to its own features. Getting this backwards is the classic way
    to produce a 0.95 AUC that evaporates in production: `pass_rate_last_5`
    computed inclusively contains the label at weight 1/5.

    Windows are scoped to (game_id, posteam) and the frame is pre-sorted by
    play_id, so nothing bleeds across games or across the two offenses.
    """
    grp = ["game_id", "posteam"]
    prev_pass = pl.col("is_pass").cast(pl.Float64).shift(1).over(grp)
    prev_epa = pl.col("epa").shift(1).over(grp)
    prev_succ = pl.col("success_f").shift(1).over(grp)

    df = df.with_columns(
        prev_is_pass=prev_pass,
        prev_epa=prev_epa,
        prev_success=prev_succ,
        pass_rate_last_5=prev_pass.rolling_mean(5, min_samples=1).over(grp),
        pass_rate_last_10=prev_pass.rolling_mean(10, min_samples=1).over(grp),
        epa_mean_last_10=prev_epa.rolling_mean(10, min_samples=1).over(grp),
        plays_so_far_game=pl.int_range(pl.len()).over(grp).cast(pl.Int32),
        pass_rate_game=prev_pass.cum_sum().over(grp) / pl.int_range(pl.len()).over(grp),
        plays_this_drive=pl.int_range(pl.len())
        .over(["game_id", "posteam", "fixed_drive"]).cast(pl.Int32),
        drive_number=pl.col("fixed_drive").rank("dense").over(grp).cast(pl.Int32),
    )

    # Success rate so far this game, split by call type. Both are cumulative
    # means over already-shifted series, so they exclude the current play.
    #
    # fill_null(0) on the numerator is NOT cosmetic. polars' `cum_sum`
    # propagates nulls positionally -- it emits null at every null input rather
    # than carrying the running total across it -- while `rolling_mean` skips
    # them. So the naive version returned null on every snap whose PREVIOUS play
    # was of the other type, which is ~50% of the frame, and the feature was
    # mostly absent rather than mostly informative. Count the denominator from
    # is_not_null (which is already null-free) and null the ratio only where
    # nothing of that type has happened yet.
    pass_succ = pl.when(prev_pass == 1).then(prev_succ).otherwise(None)
    run_succ = pl.when(prev_pass == 0).then(prev_succ).otherwise(None)
    pass_n = pass_succ.is_not_null().cum_sum().over(grp)
    run_n = run_succ.is_not_null().cum_sum().over(grp)
    return df.with_columns(
        pass_success_rate_game=pl.when(pass_n > 0).then(
            pass_succ.fill_null(0.0).cum_sum().over(grp) / pass_n),
        run_success_rate_game=pl.when(run_n > 0).then(
            run_succ.fill_null(0.0).cum_sum().over(grp) / run_n),
    )


def fit_encoder(df: pl.DataFrame, feature_set: str = "situation") -> dict[str, dict[str, int]]:
    """Stable value -> integer code map per categorical column.

    Explicit rather than leaning on polars' Categorical physical codes, which
    are assignment-ordered and therefore depend on what rows the frame happened
    to contain. The map is persisted in the artifact meta so predict.py encodes
    a 2026 game exactly the way training encoded 2024, and a play-caller the
    model never saw is encoded as missing rather than colliding with whoever
    holds that code today.
    """
    out: dict[str, dict[str, int]] = {}
    for col in FEATURE_SETS[feature_set]:
        if col not in CATEGORICAL:
            continue
        vals = sorted({v for v in df[col].cast(pl.String).to_list() if v is not None})
        out[col] = {v: i for i, v in enumerate(vals)}
    return out


def to_matrix(df: pl.DataFrame, feature_set: str, encoder: dict[str, dict[str, int]]):
    """Model matrix as a float32 numpy array. Returns (X, y, names, cat_index).

    numpy, not pandas -- pandas is not a dependency of this service and adding
    it for a dtype container would be ~50 MB for nothing. Nulls become NaN and
    LightGBM routes them down their own branch; unseen category values also
    become NaN, which is the honest encoding of "no information" and is exactly
    what we want for a coordinator in his first game.
    """
    import numpy as np

    names = FEATURE_SETS[feature_set]
    cols = []
    cat_index = []
    for i, col in enumerate(names):
        if col in CATEGORICAL:
            cat_index.append(i)
            m = encoder.get(col, {})
            codes = [m.get(v, None) for v in df[col].cast(pl.String).to_list()]
            cols.append(np.array([np.nan if c is None else float(c) for c in codes],
                                 dtype=np.float32))
        else:
            cols.append(df[col].cast(pl.Float32).to_numpy())
    X = np.column_stack(cols).astype(np.float32, copy=False)
    y = df["is_pass"].to_numpy() if "is_pass" in df.columns else None
    return X, y, names, cat_index
