"""Tests for the play-call model.

Two groups, split by what they need:

  * Everything that does not need LightGBM -- personnel parsing, the leak-free
    rolling windows, the continuity weights, the metric implementations, the
    artifact store -- runs always, on a hand-built frame with no database.
  * The fit/predict round trip is marked and skipped when lightgbm is absent, so
    a checkout that has not installed the trainer still runs a green suite.

The rolling-window tests are the load-bearing ones. A leak in `pass_rate_last_5`
does not fail loudly; it produces a better number, which is exactly why it needs
an assertion rather than a code review.
"""
from __future__ import annotations

import polars as pl
import pytest

from app.ml import continuity as C
from app.ml.artifacts import Artifact, FileArtifactStore
from app.ml.features import (FEATURES_SITUATION, PlayFrames, build_features,
                             fit_encoder, parse_personnel, to_matrix)
from app.ml.train import auc, logloss

lgb = pytest.importorskip if False else None
try:
    import lightgbm  # noqa: F401
    HAS_LGB = True
except Exception:  # pragma: no cover
    HAS_LGB = False

needs_lgb = pytest.mark.skipif(not HAS_LGB, reason="lightgbm not installed")


# --------------------------------------------------------------------------
# personnel
# --------------------------------------------------------------------------
def test_parse_personnel_short_form():
    """2021-22 nflverse form: skill positions only, FB already folded into RB."""
    assert parse_personnel("1 RB, 1 TE, 3 WR") == (1, 1, 3)
    assert parse_personnel("2 RB, 2 TE, 1 WR") == (2, 2, 1)


def test_parse_personnel_full_positional_list():
    """2023-25 FTN form lists every position, including OL and QB.

    Reading positions off by index -- "first number is RBs" -- gets 1 C as the
    running back count. Counting labelled tokens is what makes the two formats
    agree, and this is the regression test for that.
    """
    assert parse_personnel("1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 3 WR") == (1, 1, 3)
    assert parse_personnel("1 C, 2 G, 1 QB, 1 RB, 2 T, 2 TE, 2 WR") == (1, 2, 2)


def test_parse_personnel_folds_fb_into_rb():
    """The FTN form splits FB out; the nflverse form does not. Folding FB into
    RB is what makes a 2022 '21 personnel' snap and a 2024 one the same group."""
    assert parse_personnel("1 C, 1 FB, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 2 WR") == (2, 1, 2)


def test_parse_personnel_missing():
    assert parse_personnel(None) == (None, None, None)
    assert parse_personnel("") == (None, None, None)


# --------------------------------------------------------------------------
# feature frame
# --------------------------------------------------------------------------
def _frames() -> PlayFrames:
    """One game, one offence, six snaps: pass, run, pass, pass, run, pass.

    Two drives (fixed_drive 1 and 2) so the drive counters have something to do.
    """
    types = ["pass", "run", "pass", "pass", "run", "pass"]
    plays = pl.DataFrame({
        "season": [2024] * 6, "week": [1] * 6, "phase": ["reg"] * 6,
        "game_id": ["2024_01_AAA_BBB"] * 6,
        "play_id": [10, 20, 30, 40, 50, 60],
        "posteam": ["BBB"] * 6, "defteam": ["AAA"] * 6,
        "play_type": types,
        "down": [1, 2, 3, 1, 2, 3], "ydstogo": [10, 6, 4, 10, 8, 5],
        "yardline_100": [75, 71, 67, 60, 55, 52],
        "score_differential": [0.0] * 6,
        "game_seconds_remaining": [3600.0, 3570, 3540, 3500, 3470, 3440],
        "half_seconds_remaining": [1800.0, 1770, 1740, 1700, 1670, 1640],
        "quarter": [1] * 6, "posteam_timeouts_remaining": [3] * 6,
        "wp": [0.5] * 6, "xpass": [0.6, 0.4, 0.7, 0.6, 0.4, 0.7],
        "epa": [1.0, -0.5, 0.2, 0.8, -1.0, 0.3],
        "goal_to_go": [0] * 6, "success": [1, 0, 1, 1, 0, 1],
        "passer_id": ["QB1", None, "QB1", "QB1", None, "QB2"],
        "fixed_drive": [1, 1, 1, 2, 2, 2],
        "offense_personnel": ["1 RB, 1 TE, 3 WR"] * 6,
        "offense_formation": ["SHOTGUN"] * 6,
        "shotgun": [1] * 6, "no_huddle": [0] * 6, "is_motion": [0] * 6,
    })
    staff = pl.DataFrame({"season": [2024], "team": ["BBB"],
                          "offensive_play_caller_id": ["coach-a"]})
    return PlayFrames(plays=plays, staff=staff)


def test_sequence_features_do_not_leak_current_play():
    """Every rolling window must be shifted before it is rolled.

    Snap 0 has no history, so every sequence feature is null. Snap 1 sees
    exactly one prior play (a pass), so pass_rate_last_5 is 1.0 -- NOT 0.5,
    which is what an inclusive window computes when it counts snap 1's own run.
    """
    df = build_features(_frames())
    assert df["prev_is_pass"][0] is None
    assert df["pass_rate_last_5"][0] is None
    assert df["pass_rate_game"][0] is None

    assert df["prev_is_pass"][1] == 1.0
    assert df["pass_rate_last_5"][1] == pytest.approx(1.0)
    # snap 2 has seen pass, run
    assert df["pass_rate_last_5"][2] == pytest.approx(0.5)
    # snap 5 has seen pass, run, pass, pass, run
    assert df["pass_rate_last_5"][5] == pytest.approx(3 / 5)


def test_prev_play_outcome_features():
    df = build_features(_frames())
    assert df["prev_epa"][1] == pytest.approx(1.0)
    assert df["prev_success"][1] == pytest.approx(1.0)
    assert df["prev_epa"][4] == pytest.approx(0.8)


def test_split_success_rates_exclude_current_play():
    df = build_features(_frames())
    # Nothing has been run yet before snap 1, so the run split is null there.
    assert df["run_success_rate_game"][1] is None
    # By snap 3 one run has happened (snap 1, success 0).
    assert df["run_success_rate_game"][3] == pytest.approx(0.0)
    # ...and two passes, both successful.
    assert df["pass_success_rate_game"][3] == pytest.approx(1.0)


def test_drive_counters():
    df = build_features(_frames())
    assert df["plays_this_drive"].to_list() == [0, 1, 2, 0, 1, 2]
    assert df["drive_number"].to_list() == [1, 1, 1, 2, 2, 2]
    assert df["plays_so_far_game"].to_list() == [0, 1, 2, 3, 4, 5]


def test_qb_id_is_game_modal_not_per_play():
    """passer_id is null on 100% of runs, so per-play it IS the label.

    qb_id must be the same value on every snap of the game -- including the two
    runs -- or the leak is still there in a different column.
    """
    df = build_features(_frames())
    assert df["qb_id"].n_unique() == 1
    assert df["qb_id"][1] is not None       # a run play still carries the QB
    assert "passer_id" not in FEATURES_SITUATION


def test_leaky_ftn_columns_are_not_features():
    """is_play_action / is_screen_pass / is_rpo describe the call, not the
    situation before it. is_screen_pass is 0.000 on every run in the backfill."""
    from app.ml.features import FEATURES_PRESNAP
    for col in ("is_play_action", "is_screen_pass", "is_rpo", "passer_id"):
        assert col not in FEATURES_PRESNAP


def test_label():
    df = build_features(_frames())
    assert df["is_pass"].to_list() == [1, 0, 1, 1, 0, 1]


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------
def test_encoder_maps_unseen_categories_to_missing():
    """A coordinator the model never saw must encode as NaN, not collide with
    whichever id happens to hold that integer code."""
    import numpy as np
    df = build_features(_frames())
    enc = fit_encoder(df, "situation")
    assert enc["play_caller_id"] == {"coach-a": 0}

    other = df.with_columns(
        pl.lit("coach-new").cast(pl.Categorical).alias("play_caller_id"))
    X, _, names, cat_index = to_matrix(other, "situation", enc)
    col = names.index("play_caller_id")
    assert np.isnan(X[:, col]).all()
    assert col in cat_index


def test_to_matrix_shape_and_order():
    df = build_features(_frames())
    enc = fit_encoder(df, "situation")
    X, y, names, _ = to_matrix(df, "situation", enc)
    assert list(names) == FEATURES_SITUATION
    assert X.shape == (6, len(FEATURES_SITUATION))
    assert y.tolist() == [1, 0, 1, 1, 0, 1]


# --------------------------------------------------------------------------
# continuity
# --------------------------------------------------------------------------
def _cont_frames():
    staff = pl.DataFrame({
        "season": [2023, 2023, 2024, 2024],
        "team": ["AAA", "BBB", "AAA", "BBB"],
        # AAA keeps its caller; BBB's 2023 caller leaves the league, and BBB's
        # 2024 caller is the man who called plays for AAA in 2023.
        "offensive_play_caller_id": ["coach-a", "coach-gone", "coach-a", "coach-a"],
    })
    snaps = pl.DataFrame({
        "season": [2023] * 3 + [2024] * 3,
        "team": ["AAA"] * 3 + ["AAA"] * 3,
        "player_name": ["p1", "p2", "p3", "p1", "p2", "p9"],
        "offense_snaps": [1000, 500, 500, 1000, 500, 500],
    })
    return staff, snaps


def test_coach_term_full_strength_when_caller_still_calls_anywhere():
    """The case the rule exists for: a caller who moved teams keeps his own
    history at full strength, while the caller who left drops to the floor."""
    staff, snaps = _cont_frames()
    m = C.build(staff, snaps, target_season=2024)
    assert m.coach_term(2023, "AAA") == 1.0          # stayed put
    assert m.coach_term(2023, "BBB") == C.COACH_FLOOR  # left the league


def test_coach_history_at_previous_team_survives_the_move():
    """coach-a called plays for AAA in 2023 and for BBB in 2024. His 2023 AAA
    snaps must not be discounted just because BBB is a different team."""
    staff, snaps = _cont_frames()
    m = C.build(staff, snaps, target_season=2024)
    assert "coach-a" in m.active_callers
    assert m.coach_term(2023, "AAA") == 1.0


def test_roster_term_is_snap_weighted():
    """AAA's 2024 starters are p1 (1000 snaps), p2 (500), p9 (500). p1 and p2
    were there in 2023, p9 was not -> 1500/2000."""
    staff, snaps = _cont_frames()
    m = C.build(staff, snaps, target_season=2024)
    assert m.roster_term(2023, "AAA") == pytest.approx(0.75)
    assert m.roster_term(2024, "AAA") == pytest.approx(1.0)


def test_weight_combines_decay_and_continuity():
    staff, snaps = _cont_frames()
    m = C.build(staff, snaps, target_season=2024, decay=0.5)
    expected = 0.5 ** 1 * (C.W_COACH * 1.0 + C.W_ROSTER * 0.75)
    assert m.weight(2023, "AAA") == pytest.approx(expected)
    assert m.weight(2024, "AAA") == pytest.approx(1.0)


def test_weight_rejects_future_seasons():
    """decay ** negative is > 1 and would silently let post-target rows dominate
    the fit, so it raises instead."""
    staff, snaps = _cont_frames()
    m = C.build(staff, snaps, target_season=2024)
    with pytest.raises(ValueError):
        m.weight(2025, "AAA")


def test_uniform_is_in_the_weighting_grid():
    """The season-weighting rule is a hypothesis. If the search cannot pick
    'no weighting at all', it can only ever report the least-bad decay."""
    from app.ml.train import weight_schemes
    labels = [label for label, _ in weight_schemes()]
    assert "uniform" in labels
    assert any(label.startswith("continuity") for label in labels)


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def test_logloss_perfect_and_worst():
    assert logloss([1, 0], [1.0, 0.0]) < 1e-10
    assert logloss([1, 0], [0.5, 0.5]) == pytest.approx(0.6931471, abs=1e-6)


def test_auc_ranking():
    assert auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)
    assert auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == pytest.approx(0.0)
    # all-tied predictions carry no ranking information
    assert auc([0, 1], [0.5, 0.5]) == pytest.approx(0.5)


def test_auc_handles_ties_with_average_ranks():
    """Four (positive, negative) pairs: one exact tie at 0.5 scoring 0.5, and
    three clean wins -> 3.5/4. A tie-blind implementation returns 1.0 here and
    silently flatters any model that emits duplicate probabilities."""
    assert auc([0, 1, 0, 1], [0.5, 0.5, 0.1, 0.9]) == pytest.approx(0.875)


def test_evaluate_gate_is_two_sided():
    """A model that wins on AUC but loses on logloss must not pass the gate --
    a well-ranked but badly calibrated probability is not shippable behind a UI
    that displays it as a percentage."""
    from app.ml.train import evaluate
    # Baseline AUC is 0.50 here (it ranks one of the two positives below both
    # negatives) and its logloss is 0.684, so there is room to beat it on both
    # axes -- a saturated baseline would make the test vacuous.
    df = pl.DataFrame({"is_pass": [1, 0, 1, 0],
                       "xpass": [0.6, 0.4, 0.3, 0.4]})
    good = evaluate(df, [0.9, 0.1, 0.9, 0.1])
    assert good["beats_baseline"] is True

    # Perfect ranking, hopeless calibration: every positive outranks every
    # negative (AUC 1.0) but both are pinned near zero, so the true positives
    # are scored at p=0.02 and logloss is 1.96 against the baseline's 0.684.
    # The gate must reject it, because the UI renders the probability itself.
    #
    # Note the obvious construction does NOT work: [0.5001, 0.4999, 0.5002,
    # 0.0001] reads as badly calibrated but actually *beats* the baseline on
    # logloss (0.520 vs 0.684), because that confident-and-correct last
    # prediction more than pays for three coin-flips.
    bad = evaluate(df, [0.02, 0.01, 0.02, 0.01])
    over = evaluate(df, [1 - 1e-12, 1e-12, 1 - 1e-12, 1 - 1e-12])
    assert bad["auc_delta"] > 0 and bad["beats_baseline"] is False
    assert over["logloss_delta"] < 0 and over["beats_baseline"] is False


def test_evaluate_scores_model_and_baseline_on_the_same_rows():
    """Rows with a null xpass are dropped from BOTH sides. Scoring the model on
    rows the baseline cannot see would hand it free wins."""
    from app.ml.train import evaluate
    df = pl.DataFrame({"is_pass": [1, 0, 1], "xpass": [0.6, 0.4, None]})
    m = evaluate(df, [0.9, 0.1, 0.5])
    assert m["n"] == 2


# --------------------------------------------------------------------------
# artifact store
# --------------------------------------------------------------------------
def test_file_artifact_round_trip(tmp_path):
    store = FileArtifactStore(root=tmp_path)
    art = Artifact(name="playcall_situation", version="2025.1",
                   model_bytes=b"tree{}", metrics={"logloss": 0.5},
                   meta={"features": ["down"]}, trained_at=Artifact.now())
    store.save(art)
    back = store.load("playcall_situation")          # via `latest`
    assert back.model_bytes == b"tree{}"
    assert back.metrics["logloss"] == 0.5
    assert back.meta["features"] == ["down"]
    assert store.versions("playcall_situation") == ["2025.1"]


def test_file_artifact_missing():
    store = FileArtifactStore(root="/nonexistent-ml-store")
    with pytest.raises(FileNotFoundError):
        store.load("playcall_situation")


def test_db_artifact_store_is_an_honest_stub():
    """The model_artifacts table is a follow-up (it needs models.py plus an
    Alembic revision). The stub raises rather than silently no-opping."""
    from app.ml.artifacts import DbArtifactStore
    with pytest.raises(NotImplementedError):
        DbArtifactStore().load("playcall_situation")


# --------------------------------------------------------------------------
# fit / predict round trip
# --------------------------------------------------------------------------
@needs_lgb
def test_fit_and_predict_round_trip(tmp_path):
    """Train a tiny booster, save it, reload it through predict.load, and check
    the reloaded model reproduces the same probabilities.

    This is the train/serve skew guard: predict.py rebuilds features with
    build_features and re-encodes with the persisted encoder, so a drift in
    either would show up as different numbers here.
    """
    import numpy as np

    from app.ml import predict as P
    from app.ml.artifacts import Artifact, FileArtifactStore
    from app.ml.train import _fit, _predict

    frames = _frames()
    # 6 rows is too few for LightGBM to split; repeat the game with distinct ids.
    plays = pl.concat([
        frames.plays.with_columns(
            pl.lit(f"2024_0{i}_AAA_BBB").alias("game_id"),
            (pl.col("play_id") + i * 100).alias("play_id"))
        for i in range(1, 40)])
    df = build_features(PlayFrames(plays=plays, staff=frames.staff))

    enc = fit_encoder(df, "situation")
    booster = _fit(df, df, "situation", enc, None)
    direct = _predict(booster, df, "situation", enc)

    store = FileArtifactStore(root=tmp_path)
    store.save(Artifact(
        name="playcall_situation", version="test",
        model_bytes=booster.model_to_string().encode(),
        metrics={"logloss": 0.0},
        meta={"feature_set": "situation", "features": FEATURES_SITUATION,
              "encoder": enc},
        trained_at=Artifact.now()))

    loaded = P.load("situation", store=store)
    served = P.predict_frame(loaded, df).to_numpy()
    assert np.allclose(direct, served, atol=1e-9)
    assert loaded.caveats and "unverified" in loaded.caveats[0]


@needs_lgb
def test_pregame_returns_a_probability_per_situation(tmp_path):
    """Pre-game has no plays yet, so every sequence feature is null -- the same
    thing the model saw on the first snap of every training game. It must
    produce real probabilities rather than falling over on the nulls."""
    from app.ml import predict as P
    from app.ml.artifacts import Artifact, FileArtifactStore
    from app.ml.train import _fit

    frames = _frames()
    plays = pl.concat([
        frames.plays.with_columns(
            pl.lit(f"2024_0{i}_AAA_BBB").alias("game_id"),
            (pl.col("play_id") + i * 100).alias("play_id"))
        for i in range(1, 40)])
    df = build_features(PlayFrames(plays=plays, staff=frames.staff))
    enc = fit_encoder(df, "situation")
    booster = _fit(df, df, "situation", enc, None)

    store = FileArtifactStore(root=tmp_path)
    store.save(Artifact(
        name="playcall_situation", version="test",
        model_bytes=booster.model_to_string().encode(), metrics={},
        meta={"feature_set": "situation", "features": FEATURES_SITUATION,
              "encoder": enc},
        trained_at=Artifact.now()))

    out = P.pregame(P.load("situation", store=store), "BBB", "AAA", "coach-a")
    assert len(out) == len(P.PREGAME_STATES)
    for row in out:
        assert 0.0 <= row["p_pass"] <= 1.0
        assert row["p_pass"] + row["p_run"] == pytest.approx(1.0, abs=1e-4)


def test_api_does_not_import_lightgbm():
    """app/ml must stay off the API's import path. lightgbm + numpy + scipy are
    ~120 MB of RSS and the VM has 1-2 GB; app/db/upsert.py documents the same
    rule for polars. Importing app.main must not drag the trainer in.
    """
    import subprocess
    import sys
    code = ("import sys; import app.main; "
            "assert 'lightgbm' not in sys.modules, sorted(m for m in sys.modules "
            "if 'lightgbm' in m)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
