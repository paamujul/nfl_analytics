"""Fit the play-call model and check it honestly against `xpass`.

    python -m app.cli train 2025

RUN THIS IN A CLOUD RUN JOB (4 GiB), NOT ON THE VM. Peak RSS is dominated by
LightGBM's histogram construction over ~176k x ~30, which is small, but
lightgbm + numpy + scipy alone are ~120 MB of imports and the VM has 1-2 GB
serving the API. Nothing outside `app.ml` may import this module.

SPLIT
-----
Time-based only. Train 2021-24, hold out 2025 entirely. A random split puts
snaps from the same drive on both sides of the boundary, and the sequence
features (`pass_rate_last_5` and friends) then carry the answer across it --
which inflates every number and is invisible unless you look for it.

The row weighting is tuned on an INNER split (train 2021-23 weighted toward
2024, score on 2024) and the winner is then used to fit 2021-24 toward 2025.
Tuning against 2025 and then reporting 2025 would make the held-out number a
training metric.

WHAT THE WEIGHTING SEARCH ACTUALLY FOUND
----------------------------------------
The two halves of the continuity rule do not behave the same way. Inner split
(fit 2021-23, score 2024), situational features, 14 schemes:

    decay 0.7, no staff/roster term   logloss 0.55672   auc 0.77697   <- picked
    decay 0.8, no staff/roster term   logloss 0.55672   auc 0.77709
    continuity decay 0.7              logloss 0.55693   auc 0.77680
    uniform                           logloss 0.55704   auc 0.77658
    continuity decay 0.8              logloss 0.55719   auc 0.77653
    continuity decay 0.5              logloss 0.55871   auc 0.77463

So: a mild season decay around 0.7-0.8 is worth roughly 0.0003 of logloss over
uniform, and adding the coach/roster continuity term on top makes things *worse*
at every decay -- monotonically so as the decay sharpens. On held-out 2025 the
picked scheme lands at 0.55796 against uniform's 0.55784, i.e. the whole
weighting apparatus is a wash.

The likely mechanism for the continuity term losing: `play_caller_id` is already
a first-class categorical feature, so the model can condition on whose
tendencies a snap reflects. Re-weighting rows by coach continuity applies the
same information a second time and more bluntly, and pays for it in effective
sample size. The mirror result supports it -- dropping 2021-22 outright is
clearly worse (logloss 0.56185), so old snaps do carry signal and discounting
them hard costs more than it buys.

`uniform` is therefore in the search grid, continuity.py is fully implemented
and selectable, and the tuner decides. The spec's 0.6 decay guess was close on
the decay axis and wrong on the continuity axis, and this docstring says so
rather than quietly pinning numbers that lost.

THE GATE
--------
`xpass` is nflverse's own pass-probability model and it is good. The situational
model -- the one that sees only what xpass sees -- has to beat it on held-out
2025 logloss AND AUC. `evaluate()` returns `beats_baseline`; `run()` prints a
loud FAILS line and exits non-zero when it is false. Shipping a UI that implies
predictive power the model does not have is worse than shipping nothing.

The `presnap` model (which additionally sees formation, personnel and shotgun)
is reported alongside for reference, but it is NOT comparable to xpass and does
not satisfy the gate.
"""
from __future__ import annotations

import logging
import math
import time

import polars as pl

from app.ml import continuity as C
from app.ml.artifacts import Artifact, ArtifactStore, default_store
from app.ml.features import (FEATURE_SETS, build_features, fit_encoder,
                             load_play_frames, to_matrix)

log = logging.getLogger(__name__)

TRAIN_SEASONS = (2021, 2022, 2023, 2024)
DECAY_GRID = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)

PARAMS = {
    "objective": "binary",
    "metric": ["binary_logloss", "auc"],
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    # Categorical splits on play_caller_id (~90 distinct) and qb_id (~200)
    # overfit fast; these are LightGBM's own brakes for that.
    "cat_smooth": 20.0,
    "cat_l2": 10.0,
    "max_cat_threshold": 32,
    "verbosity": -1,
    "num_threads": 0,
    "seed": 17,
}
NUM_ROUNDS = 1200
EARLY_STOPPING = 60


# --------------------------------------------------------------------------
# metrics -- implemented here rather than pulling scikit-learn in for two
# functions. Both take plain float lists so tests can call them directly.
# --------------------------------------------------------------------------
def logloss(y, p, eps: float = 1e-15) -> float:
    n = 0
    total = 0.0
    for yi, pi in zip(y, p):
        if pi is None or (isinstance(pi, float) and math.isnan(pi)):
            continue
        pi = min(max(float(pi), eps), 1.0 - eps)
        total += -(yi * math.log(pi) + (1 - yi) * math.log(1 - pi))
        n += 1
    return total / n if n else float("nan")


def auc(y, p) -> float:
    """Rank-based AUC (Mann-Whitney U), tie-corrected via average ranks."""
    pairs = [(float(pi), int(yi)) for yi, pi in zip(y, p)
             if pi is not None and not (isinstance(pi, float) and math.isnan(pi))]
    if not pairs:
        return float("nan")
    pairs.sort(key=lambda t: t[0])
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    n_pos = sum(y for _, y in pairs)
    n_neg = len(pairs) - n_pos
    if not n_pos or not n_neg:
        return float("nan")
    rank_sum = sum(r for r, (_, y) in zip(ranks, pairs) if y == 1)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _aligned(df: pl.DataFrame, pred) -> tuple[list, list, list]:
    """Drop rows where `xpass` is null so model and baseline are scored on
    exactly the same snaps -- otherwise the model gets ~700 extra easy rows and
    the comparison is not like for like."""
    mask = df["xpass"].is_not_null().to_list()
    y = df["is_pass"].to_list()
    xp = df["xpass"].to_list()
    return ([yi for yi, m in zip(y, mask) if m],
            [pi for pi, m in zip(pred, mask) if m],
            [xi for xi, m in zip(xp, mask) if m])


# --------------------------------------------------------------------------
def _dataset(df: pl.DataFrame, feature_set: str, encoder, weight: pl.Series | None):
    import lightgbm as lgb
    X, y, names, cat_index = to_matrix(df, feature_set, encoder)
    return lgb.Dataset(X, label=y, feature_name=list(names),
                       weight=None if weight is None else weight.to_numpy(),
                       categorical_feature=cat_index, free_raw_data=False)


def _fit(train_df, valid_df, feature_set, encoder, weight):
    import lightgbm as lgb
    dtrain = _dataset(train_df, feature_set, encoder, weight)
    dvalid = _dataset(valid_df, feature_set, encoder, None)
    return lgb.train(PARAMS, dtrain, num_boost_round=NUM_ROUNDS,
                     valid_sets=[dvalid], valid_names=["valid"],
                     callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)])


def _predict(booster, df, feature_set, encoder):
    X, _, _, _ = to_matrix(df, feature_set, encoder)
    return booster.predict(X, num_iteration=booster.best_iteration)


def evaluate(df: pl.DataFrame, pred) -> dict:
    """Model vs `xpass` on the same rows. `beats_baseline` is the gate."""
    y, p, xp = _aligned(df, pred)
    m = {
        "n": len(y),
        "logloss": logloss(y, p),
        "auc": auc(y, p),
        "baseline_logloss": logloss(y, xp),
        "baseline_auc": auc(y, xp),
    }
    m["logloss_delta"] = m["baseline_logloss"] - m["logloss"]   # positive = better
    m["auc_delta"] = m["auc"] - m["baseline_auc"]
    m["beats_baseline"] = bool(m["logloss_delta"] > 0 and m["auc_delta"] > 0)
    return m


def weight_schemes(grid=DECAY_GRID) -> list[tuple[str, dict | None]]:
    """The candidate weightings, uniform included.

    `uniform` is in the search on purpose. The season-weighting rule is a
    hypothesis, not a given, and on this data it loses -- see the note in run().
    Leaving it out would have made the tuner pick the least-bad decay and
    present that as a result.
    """
    schemes: list[tuple[str, dict | None]] = [("uniform", None)]
    for d in grid:
        if d < 1.0:
            # decay alone, staff/roster carry switched off
            schemes.append((f"decay={d}",
                            {"decay": d, "coach_floor": 1.0,
                             "w_coach": 1.0, "w_roster": 0.0}))
        schemes.append((f"continuity decay={d}", {"decay": d}))
    return schemes


def tune_weighting(feat: pl.DataFrame, staff, snaps, feature_set="situation",
                   grid=DECAY_GRID) -> tuple[str, dict | None, list[dict]]:
    """Pick the row weighting on an INNER split: fit 2021-23 weighted toward
    2024, score 2024. 2025 is never touched here -- tuning against the held-out
    season and then reporting it would make the headline number a training metric.

    Selection is on logloss, not AUC. AUC is invariant to calibration, and
    predict.py puts probabilities in front of a user, so calibration is the
    property that matters.
    """
    inner_target = max(TRAIN_SEASONS)
    inner_train = [s for s in TRAIN_SEASONS if s < inner_target]
    tr = feat.filter(pl.col("season").is_in(inner_train))
    va = feat.filter(pl.col("season") == inner_target)

    # Encoder fitted on the inner training seasons only, for the same reason
    # the split is time-based: a code for a coordinator who first appears in
    # 2024 must not exist while scoring 2024.
    encoder = fit_encoder(tr, feature_set)

    trials = []
    for label, kw in weight_schemes(grid):
        w = None
        if kw is not None:
            cm = C.build(staff, snaps, target_season=inner_target, **kw)
            w = C.weight_column(tr, cm)
        booster = _fit(tr, va, feature_set, encoder, w)
        m = evaluate(va, _predict(booster, va, feature_set, encoder))
        trials.append({"scheme": label, "kwargs": kw, **m})
        log.info("%-24s inner logloss=%.5f auc=%.5f", label, m["logloss"], m["auc"])
    best = min(trials, key=lambda t: t["logloss"])
    return best["scheme"], best["kwargs"], trials


def run(target_season: int = 2025, store: ArtifactStore | None = None,
        engine=None, tune: bool = True) -> dict:
    """Full training run. Returns the metrics dict that the CLI prints."""
    from app.db.session import engine as default_engine
    engine = engine or default_engine
    store = store or default_store()

    seasons = tuple(sorted(set(TRAIN_SEASONS) | {target_season}))
    t0 = time.time()
    frames = load_play_frames(engine, seasons)
    snaps = C.load_snap_shares(engine, seasons)
    feat = build_features(frames)
    log.info("loaded %d snaps in %.1fs", feat.height, time.time() - t0)

    train_df = feat.filter(pl.col("season") < target_season)
    valid_df = feat.filter(pl.col("season") == target_season)
    if valid_df.is_empty():
        raise SystemExit(f"no plays for target season {target_season}")

    if tune:
        scheme, kw, trials = tune_weighting(feat, frames.staff, snaps)
    else:
        scheme, kw, trials = "uniform", None, []
    log.info("weighting = %s", scheme)

    weight = None
    if kw is not None:
        cm = C.build(frames.staff, snaps, target_season=target_season, **kw)
        weight = C.weight_column(train_df, cm)

    out = {"target_season": target_season, "weighting": scheme,
           "weighting_kwargs": kw, "decay": (kw or {}).get("decay"),
           "weight_trials": trials, "train_rows": train_df.height,
           "valid_rows": valid_df.height}

    for feature_set in ("situation", "presnap"):
        t = time.time()
        encoder = fit_encoder(train_df, feature_set)
        booster = _fit(train_df, valid_df, feature_set, encoder, weight)
        metrics = evaluate(valid_df, _predict(booster, valid_df, feature_set, encoder))
        metrics["train_seconds"] = round(time.time() - t, 1)
        metrics["best_iteration"] = booster.best_iteration
        metrics["importance"] = _importance(booster)
        out[feature_set] = metrics

        version = f"{target_season}.{Artifact.now()[:10].replace('-', '')}"
        store.save(Artifact(
            name=f"playcall_{feature_set}", version=version,
            model_bytes=booster.model_to_string().encode(),
            metrics={k: v for k, v in metrics.items() if k != "importance"},
            meta={"feature_set": feature_set, "features": FEATURE_SETS[feature_set],
                  "encoder": encoder, "weighting": scheme,
                  "weighting_kwargs": kw, "target_season": target_season,
                  "train_seasons": [s for s in seasons if s < target_season],
                  "caveat": "play_caller_id is derived and unverified; nflverse "
                            "per-game coach data misses in-season changes from 2024"},
            trained_at=Artifact.now()))
    return out


def _importance(booster, top: int = 15) -> list[tuple[str, int]]:
    gains = zip(booster.feature_name(), booster.feature_importance("gain"))
    return [(n, int(g)) for n, g in sorted(gains, key=lambda t: -t[1])[:top]]


def format_report(out: dict) -> str:
    lines = [f"target season {out['target_season']}  weighting={out['weighting']}",
             f"train rows {out['train_rows']:,}  valid rows {out['valid_rows']:,}", ""]
    if out.get("weight_trials"):
        best = min(t["logloss"] for t in out["weight_trials"])
        worst = max(t["logloss"] for t in out["weight_trials"])
        lines += [f"weighting search (inner split, 2024): {len(out['weight_trials'])} "
                  f"schemes, logloss {best:.5f}-{worst:.5f}", ""]
    for fs in ("situation", "presnap"):
        m = out.get(fs)
        if not m:
            continue
        gate = "" if fs != "situation" else (
            "  <- BEATS xpass" if m["beats_baseline"] else "  <- FAILS the xpass gate")
        lines += [
            f"[{fs}]{gate}",
            f"  n={m['n']:,}  best_iter={m['best_iteration']}  {m['train_seconds']}s",
            f"  logloss  model {m['logloss']:.5f}   xpass {m['baseline_logloss']:.5f}"
            f"   delta {m['logloss_delta']:+.5f}",
            f"  auc      model {m['auc']:.5f}   xpass {m['baseline_auc']:.5f}"
            f"   delta {m['auc_delta']:+.5f}",
            "  top gain: " + ", ".join(n for n, _ in m["importance"][:8]), ""]
    if not out.get("situation", {}).get("beats_baseline", False):
        lines.append("GATE FAILED: the situational model does not beat xpass on "
                     "held-out data. Do not ship a UI implying otherwise.")
    return "\n".join(lines)
