"""Calculation identity only; no changes to estimation or optimization mathematics."""

import hashlib
import json
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from app.analytics.models import ReturnPanel

ENGINE_VERSION = "1.0.0"


@lru_cache
def engine_identity() -> dict:
    digest = hashlib.sha256()
    directory = Path(__file__).parent
    for name in (
        "models.py",
        "data_loader.py",
        "estimators.py",
        "constraints.py",
        "metrics.py",
        "optimizer.py",
        "frontier.py",
        "cross_validation.py",
        "bootstrap.py",
        "provenance.py",
    ):
        digest.update(name.encode())
        digest.update(directory.joinpath(name).read_bytes().replace(b"\r\n", b"\n"))
    return {
        "name": "qcross-portfolio-engine",
        "version": ENGINE_VERSION,
        "source_sha256": digest.hexdigest(),
        "dependencies": {
            name: version(name)
            for name in ("numpy", "pandas", "scipy", "scikit-learn", "cvxpy", "clarabel")
        },
    }


def panel_provenance(panel: ReturnPanel) -> dict:
    header = {"assets": panel.assets, "months": [str(month) for month in panel.returns.index]}
    digest = hashlib.sha256(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    digest.update(b"\n")
    digest.update(panel.values.astype("<f8").tobytes(order="C"))
    return {
        "engine": engine_identity(),
        "returns_matrix_sha256": digest.hexdigest(),
        "matrix_encoding": "canonical JSON {assets,months}, LF, row-major little-endian float64",
        "source_series": [
            {
                key: row.get(key)
                for key in (
                    "instrument_id",
                    "series_id",
                    "source",
                    "currency",
                    "series_version",
                    "series_sha256",
                    "last_updated_at",
                    "observation_count",
                )
            }
            for row in panel.metadata
        ],
    }
