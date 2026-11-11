"""Calculation identity only; no changes to estimation or optimization mathematics."""

import hashlib
import json
from functools import lru_cache, wraps
from importlib.metadata import version
from inspect import signature
from pathlib import Path

from app.analytics.models import ReturnPanel

ENGINE_VERSION = "2.3.0"


def reproducible(function):
    """Include estimator parameters and effective settings in calculation identity."""

    @wraps(function)
    def calculate(*args, **kwargs):
        result = function(*args, **kwargs)
        bound = signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        settings = {k: v for k, v in bound.arguments.items() if k != "panel"}
        identity = {
            "panel": result["reproducibility"]["reference_panel_sha256"],
            "engine": engine_identity(),
            "settings": settings,
            "asset_parameters": result["covariance_parameters"],
            "differential_parameters": result["differential_covariance_parameters"],
            "sample_fingerprint": result.get("sample_fingerprint"),
        }
        result["reproducibility"]["calculation_sha256"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, default=str).encode()
        ).hexdigest()
        return result

    return calculate


@lru_cache
def engine_identity() -> dict:
    digest = hashlib.sha256()
    directory = Path(__file__).parent
    for name in (
        "models.py",
        "references.py",
        "data_loader.py",
        "estimators.py",
        "constraints.py",
        "metrics.py",
        "optimizer.py",
        "frontier.py",
        "cross_validation.py",
        "splitting.py",
        "bootstrap.py",
        "bootstrap_metrics.py",
        "paired_comparison.py",
        "efficiency_distance.py",
        "rolling_efficiency.py",
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
    reference_digest = hashlib.sha256(digest.digest())
    reference_digest.update(
        json.dumps(
            {
                "basis": panel.return_basis,
                "risk_free_source": panel.risk_free_source,
                "references": panel.reference_metadata,
            },
            sort_keys=True,
        ).encode()
    )
    if panel.risk_free_returns is not None:
        reference_digest.update(panel.risk_free_returns.astype("<f8").tobytes())
    return {
        "reference_panel_sha256": reference_digest.hexdigest(),
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
