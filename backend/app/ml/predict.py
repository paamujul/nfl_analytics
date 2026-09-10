"""Serving side: a pre-game distribution for a matchup, and per-play updates as
the drive unfolds.

THE IN-GAME UPDATE IS FEATURE ENGINEERING, NOT ONLINE LEARNING. Nothing here
touches the booster's weights. `pass_rate_last_5`, `epa_mean_last_10`,
`prev_success` and the rest are recomputed from the sequence so far, and the
prediction moves because its inputs moved. Fitting during a live game on a few
dozen snaps would be unstable, unreproducible, and impossible to debug after the
fact -- you could not tell a real tendency shift from three noisy series.

CAVEAT TO SURFACE WITH ANY PREDICTION
-------------------------------------
`play_caller_id` is derived, not sourced. Every row of `coaching_staff` carries
verified = 0; the coordinator list is model-generated; and nflverse's per-game
coach feed degrades from 2024, recording one coach per team-season and missing
in-season changes (NYJ 2024 attributes all 17 games to Saleh, though Ulbrich
coached 12). `PLAY_CALLER_CAVEAT` is exported so a caller can put it on screen
rather than reinventing the wording.
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.ml.artifacts import ArtifactStore, default_store
from app.ml.features import FEATURE_SETS, build_features, load_play_frames, to_matrix

PLAY_CALLER_CAVEAT = (
    "Play-caller attribution is derived and unverified: coordinator assignments "
    "are model-generated, and the upstream per-game coach feed records one coach "
    "per team-season from 2024 onward, so in-season changes are missed."
)


@dataclass
class LoadedModel:
    """A booster plus the encoder and feature list it was fitted with.

    All three travel together on purpose. Scoring with a feature order or a
    category map that does not match training produces confident nonsense rather
    than an error, so they are never loaded separately.
    """
    booster: object
    feature_set: str
    features: list[str]
    encoder: dict
    metrics: dict
    version: str

    @property
    def caveats(self) -> list[str]:
        return [PLAY_CALLER_CAVEAT]


def load(feature_set: str = "situation", version: str | None = None,
         store: ArtifactStore | None = None) -> LoadedModel:
    import lightgbm as lgb
    store = store or default_store()
    art = store.load(f"playcall_{feature_set}", version)
    booster = lgb.Booster(model_str=art.model_bytes.decode())
    return LoadedModel(booster=booster, feature_set=feature_set,
                       features=art.meta.get("features", FEATURE_SETS[feature_set]),
                       encoder=art.meta.get("encoder", {}),
                       metrics=art.metrics, version=art.version)


def predict_frame(model: LoadedModel, feat: pl.DataFrame) -> pl.Series:
    """P(pass) for every row of an already-built feature frame."""
    X, _, _, _ = to_matrix(feat, model.feature_set, model.encoder)
    return pl.Series("p_pass", model.booster.predict(X), dtype=pl.Float64)


# --------------------------------------------------------------------------
# pre-game
# --------------------------------------------------------------------------
# Down/distance/field-position states a pre-game view is actually asked about.
# Coarse on purpose: this is "what does this offence do on 2nd and long",
# not a 1,000-cell grid nobody reads.
PREGAME_STATES = [
    ("1st & 10", dict(down=1, ydstogo=10, yardline_100=70)),
    ("2nd & short", dict(down=2, ydstogo=3, yardline_100=60)),
    ("2nd & long", dict(down=2, ydstogo=9, yardline_100=70)),
    ("3rd & short", dict(down=3, ydstogo=2, yardline_100=55)),
    ("3rd & medium", dict(down=3, ydstogo=6, yardline_100=60)),
    ("3rd & long", dict(down=3, ydstogo=11, yardline_100=65)),
    ("red zone 1st & 10", dict(down=1, ydstogo=10, yardline_100=18)),
    ("goal to go", dict(down=2, ydstogo=3, yardline_100=3, goal_to_go=1)),
]

_NEUTRAL = dict(
    score_differential=0.0, game_seconds_remaining=1800.0,
    half_seconds_remaining=900.0, quarter=2, posteam_timeouts_remaining=3,
    wp=0.5, goal_to_go=0, xpass=None,
    pass_rate_last_5=None, pass_rate_last_10=None, epa_mean_last_10=None,
    pass_rate_game=None, pass_success_rate_game=None, run_success_rate_game=None,
    plays_so_far_game=0, plays_this_drive=0, drive_number=1,
    prev_is_pass=None, prev_success=None, prev_epa=None,
    offense_formation=None, personnel_group=None, n_rb=None, n_te=None, n_wr=None,
    shotgun=None, no_huddle=None, is_motion=None,
)


def pregame(model: LoadedModel, posteam: str, defteam: str, play_caller_id: str,
            qb_id: str | None = None, states=PREGAME_STATES) -> list[dict]:
    """P(pass) across the standard situations, before a snap has been played.

    Every sequence feature is null, which is the truthful encoding of "no plays
    yet" and the same thing the model saw for the first snap of each game in
    training -- so this is in-distribution, not an extrapolation.

    `xpass` is null too: nflverse computes it per play and there is none to
    borrow before kickoff. The model was trained with 0.39% of rows missing
    xpass, so it has a branch for that, but it is a real degradation and the
    pre-game numbers should be read as coarser than the in-game ones.
    """
    rows = []
    for label, state in states:
        row = dict(_NEUTRAL)
        row.update(state)
        row.update(posteam=posteam, defteam=defteam,
                   play_caller_id=play_caller_id, qb_id=qb_id)
        rows.append((label, row))

    feat = _frame([r for _, r in rows], model.feature_set)
    probs = predict_frame(model, feat).to_list()
    return [{"situation": label, "p_pass": round(p, 4), "p_run": round(1 - p, 4)}
            for (label, _), p in zip(rows, probs)]


def _frame(rows: list[dict], feature_set: str) -> pl.DataFrame:
    """Build a feature frame from plain dicts, with the dtypes to_matrix wants."""
    names = FEATURE_SETS[feature_set]
    data = {n: [r.get(n) for r in rows] for n in names}
    return pl.DataFrame(data, strict=False)


# --------------------------------------------------------------------------
# in-game
# --------------------------------------------------------------------------
def in_game(model: LoadedModel, engine, game_id: str, posteam: str) -> list[dict]:
    """Replay one team's offence through a completed game, one prediction per snap.

    Reuses build_features rather than a parallel implementation, so the rolling
    windows here are byte-for-byte the ones training used -- including the
    shift(1) that keeps a play out of its own features. A second, "serving"
    copy of that logic is how train/serve skew gets in.
    """
    season = int(game_id.split("_")[0])
    frames = load_play_frames(engine, (season,))
    frames = type(frames)(
        plays=frames.plays.filter(pl.col("game_id") == game_id),
        staff=frames.staff)
    feat = build_features(frames).filter(pl.col("posteam") == posteam)
    if feat.is_empty():
        return []
    probs = predict_frame(model, feat)
    return [
        {"play_id": pid, "quarter": q, "down": d, "ydstogo": y,
         "p_pass": round(p, 4), "actual": "pass" if a else "run",
         "xpass": None if xp is None else round(xp, 4)}
        for pid, q, d, y, p, a, xp in zip(
            feat["play_id"], feat["quarter"], feat["down"], feat["ydstogo"],
            probs, feat["is_pass"], feat["xpass"])
    ]
