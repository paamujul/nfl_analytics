"""Where a trained model lives.

The plan is a `model_artifacts` table (bytea + version + metrics + trained_at):
a LightGBM booster is a few MB, the API already holds a database handle, and a
table avoids adding GCS along with its bucket, its IAM binding and its
credential rotation for the sake of one blob.

That table is NOT created here. Adding it means editing `app/db/models.py` and
cutting an Alembic revision, neither of which this change owns, so:

    * `FileArtifactStore` (the default) writes to STORAGE_DIR/models/.
    * `DbArtifactStore` is stubbed against the intended schema and raises.

Both sit behind `ArtifactStore`, so the swap is a constructor change in
train.py/predict.py and nothing else. See the module docstring in train.py for
the follow-up.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.config import STORAGE_DIR

MODEL_DIR = Path(STORAGE_DIR) / "models"


@dataclass(frozen=True)
class Artifact:
    """A serialized booster plus everything needed to interpret it later."""
    name: str
    version: str
    model_bytes: bytes
    metrics: dict
    meta: dict
    trained_at: str

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ArtifactStore(Protocol):
    def save(self, artifact: Artifact) -> str: ...
    def load(self, name: str, version: str | None = None) -> Artifact: ...
    def versions(self, name: str) -> list[str]: ...


class FileArtifactStore:
    """STORAGE_DIR/models/<name>/<version>.{txt,json}.

    The booster is written as LightGBM's own text format rather than pickle:
    it survives a lightgbm upgrade, and it is diffable when a prediction looks
    wrong. `latest` is a plain file holding a version string -- no symlink,
    because a container image layer will not carry one reliably.
    """

    def __init__(self, root: Path = MODEL_DIR):
        self.root = Path(root)

    def _dir(self, name: str) -> Path:
        return self.root / name

    def save(self, artifact: Artifact) -> str:
        d = self._dir(artifact.name)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{artifact.version}.txt").write_bytes(artifact.model_bytes)
        (d / f"{artifact.version}.json").write_text(json.dumps({
            "name": artifact.name, "version": artifact.version,
            "metrics": artifact.metrics, "meta": artifact.meta,
            "trained_at": artifact.trained_at,
        }, indent=2, default=str))
        (d / "latest").write_text(artifact.version)
        return str(d / f"{artifact.version}.txt")

    def versions(self, name: str) -> list[str]:
        d = self._dir(name)
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.txt"))

    def load(self, name: str, version: str | None = None) -> Artifact:
        d = self._dir(name)
        if version is None:
            latest = d / "latest"
            if not latest.is_file():
                raise FileNotFoundError(f"no model {name!r} under {self.root}")
            version = latest.read_text().strip()
        blob = d / f"{version}.txt"
        if not blob.is_file():
            raise FileNotFoundError(f"no model {name!r} version {version!r}")
        side = json.loads((d / f"{version}.json").read_text())
        return Artifact(name=name, version=version, model_bytes=blob.read_bytes(),
                        metrics=side.get("metrics", {}), meta=side.get("meta", {}),
                        trained_at=side.get("trained_at", ""))


class DbArtifactStore:
    """FOLLOW-UP -- needs a `model_artifacts` table and an Alembic revision.

        model_artifacts(
            name        VARCHAR(64)   NOT NULL,
            version     VARCHAR(32)   NOT NULL,
            model       BYTEA         NOT NULL,   -- LightGBM text dump
            metrics     JSON          NOT NULL,
            meta        JSON          NOT NULL,
            trained_at  TIMESTAMPTZ   NOT NULL,
            PRIMARY KEY (name, version)
        )

    Once that exists this becomes a ~20-line SQLAlchemy implementation of
    ArtifactStore and the default in train.py/predict.py flips over. Until then
    it raises rather than pretending, so nothing ships half-wired.
    """

    def __init__(self, session_factory=None):
        self._session_factory = session_factory

    def _unavailable(self):
        raise NotImplementedError(
            "model_artifacts table not created yet -- add it to app/db/models.py "
            "with an Alembic revision, then implement DbArtifactStore. "
            "Use FileArtifactStore in the meantime.")

    save = load = versions = lambda self, *a, **k: self._unavailable()


def default_store() -> ArtifactStore:
    return FileArtifactStore()
