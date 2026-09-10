"""Play-call prediction: P(pass) for the next snap, given game state and how
the drive has gone so far.

Deliberately import-free at package level. LightGBM, numpy and scipy together
are ~120 MB of RSS, and the API process imports `app.analytics` and `app.api`
but must never pay for the trainer -- the VM has 1-2 GB. `app/db/upsert.py`
documents the same discipline for polars. So: no `from app.ml.train import ...`
at module scope anywhere outside this package, and nothing heavy here.

    features.py    play frame -> model matrix (leak-free rolling windows)
    continuity.py  per-row training weights (season decay x staff/roster carry)
    train.py       LightGBM fit, time-based split, honest gate against `xpass`
    predict.py     pre-game distribution + per-play in-game updates
    artifacts.py   model save/load (local file today, DB table is a follow-up)
"""
