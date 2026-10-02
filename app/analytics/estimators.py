"""Monthly covariance estimators and explicit numerical conditioning diagnostics."""

import numpy as np
from sklearn.covariance import LedoitWolf
from sklearn.model_selection import KFold

from app.analytics.models import AnalyticsError, CovarianceEstimate


def validate_covariance(matrix: np.ndarray) -> tuple[np.ndarray, dict]:
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or not np.isfinite(matrix).all():
        raise AnalyticsError("Covariance must be square and numerically finite.")
    scale = max(float(np.max(np.abs(matrix))), 1e-14)
    symmetry_error = float(np.max(np.abs(matrix - matrix.T)))
    if symmetry_error > scale * 1e-8:
        raise AnalyticsError("Covariance is not symmetric within numerical tolerance.")
    symmetric = (matrix + matrix.T) / 2
    eigenvalues, vectors = np.linalg.eigh(symmetric)
    if eigenvalues[0] < -scale * 1e-8:
        raise AnalyticsError("Covariance has materially negative eigenvalues.")
    floor = max(float(eigenvalues[-1]) * 1e-10, 1e-14)
    adjusted = np.maximum(eigenvalues, floor)
    nearly_singular = bool(eigenvalues[0] < floor)
    result = (vectors * adjusted) @ vectors.T if nearly_singular else symmetric
    condition = None if eigenvalues[0] <= 0 else float(eigenvalues[-1] / eigenvalues[0])
    diagnostics = {
        "finite": True,
        "symmetry_error": symmetry_error,
        "positive_semidefinite": bool(eigenvalues[0] >= -scale * 1e-8),
        "eigenvalues_before": eigenvalues.tolist(),
        "eigenvalues_after": adjusted.tolist(),
        "condition_number_before": condition,
        "condition_number_after": float(adjusted[-1] / adjusted[0]),
        "nearly_singular": nearly_singular,
        "regularization": "eigenvalue_floor" if nearly_singular else "none",
        "eigenvalue_floor": floor if nearly_singular else None,
        "frobenius_change": float(np.linalg.norm(result - matrix)),
    }
    return result, diagnostics


def nonlinear_covariance(values: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf (2020), equations 4.3, 4.7-4.9, p <= n branch.

    Demean and use n=T-1 as in the authors' analshrink.m. The supported application
    has at most 15 assets and at least 120 months, so p>n is deliberately rejected.
    Rank-deficient inputs are rejected, never replaced with a heuristic estimator.
    """
    values = np.asarray(values, dtype=float)
    n, p = len(values) - 1, values.shape[1]
    if n < 12 or p > n:
        raise AnalyticsError("Analytical nonlinear shrinkage requires n=T-1 >= max(12, assets).")
    centered = values - values.mean(axis=0)
    eigenvalues, vectors = np.linalg.eigh(centered.T @ centered / n)
    if eigenvalues.sum() <= 0 or np.any(eigenvalues / eigenvalues.sum() < 1e-8):
        raise AnalyticsError("Analytical nonlinear shrinkage input is singular or nearly singular.")
    h = n ** (-1 / 3)
    bandwidth = h * eigenvalues[None, :]
    x = (eigenvalues[:, None] - eigenvalues[None, :]) / bandwidth
    root5 = np.sqrt(5)
    density = np.mean(3 / (4 * root5) * np.maximum(1 - x**2 / 5, 0) / bandwidth, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        hilbert_kernel = -3 * x / (10 * np.pi) + 3 / (4 * root5 * np.pi) * (1 - x**2 / 5) * np.log(
            np.abs((root5 - x) / (root5 + x))
        )
    boundary = np.abs(x) == root5
    hilbert_kernel[boundary] = -3 * x[boundary] / (10 * np.pi)
    hilbert = np.mean(hilbert_kernel / bandwidth, axis=1)
    ratio = p / n
    shrunk = eigenvalues / (
        (np.pi * ratio * eigenvalues * density) ** 2
        + (1 - ratio - np.pi * ratio * eigenvalues * hilbert) ** 2
    )
    return (vectors * shrunk) @ vectors.T


def linear_covariance(values: np.ndarray, intensity: float, target: str) -> np.ndarray:
    if not 0 <= intensity <= 1 or target not in {"identity", "diagonal"}:
        raise AnalyticsError("Invalid linear shrinkage intensity or target.")
    sample = np.cov(values, rowvar=False, ddof=1)
    destination = (
        np.eye(sample.shape[0]) * np.trace(sample) / sample.shape[0]
        if target == "identity"
        else np.diag(np.diag(sample))
    )
    return (1 - intensity) * sample + intensity * destination


def gaussian_nll(validation: np.ndarray, mean: np.ndarray, covariance: np.ndarray) -> float:
    """Per-observation Gaussian NLL; validation residuals use the training mean."""
    factor = np.linalg.cholesky(covariance)
    residuals = np.linalg.solve(factor, (validation - mean).T)
    return float(
        0.5
        * (
            validation.shape[1] * np.log(2 * np.pi)
            + 2 * np.log(np.diag(factor)).sum()
            + np.mean(np.sum(residuals**2, axis=0))
        )
    )


def select_shrinkage(values: np.ndarray, folds: int = 5, target: str = "identity") -> dict:
    if not 2 <= folds <= 10 or len(values) < folds * 2:
        raise AnalyticsError(
            "Shrinkage CV requires 2 to 10 folds and at least two months per fold."
        )
    grid = np.linspace(0, 1, 101)
    totals = np.zeros(len(grid))
    splits = list(KFold(n_splits=folds, shuffle=False).split(values))
    for train, validation in splits:
        training = values[train]
        for i, intensity in enumerate(grid):
            matrix, _ = validate_covariance(linear_covariance(training, float(intensity), target))
            totals[i] += len(validation) * gaussian_nll(
                values[validation], training.mean(0), matrix
            )
    scores = totals / len(values)
    best = int(np.argmin(scores))
    return {
        "target": target,
        "intensity": float(grid[best]),
        "folds": folds,
        "criterion": "validation_gaussian_nll",
        "grid": grid.tolist(),
        "nll_scores": scores.tolist(),
    }


def estimate_covariance(
    values: np.ndarray,
    method: str = "sample",
    *,
    folds: int = 5,
    target: str = "identity",
    fixed_parameters: dict | None = None,
) -> CovarianceEstimate:
    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or len(values) < 2 or values.shape[1] < 2 or not np.isfinite(values).all():
        raise AnalyticsError("Covariance estimation requires a finite monthly return matrix.")
    parameters = {}
    if method == "sample":
        matrix = np.cov(values, rowvar=False, ddof=1)
    elif method == "ledoit_wolf":
        estimator = LedoitWolf().fit(values)
        matrix = estimator.covariance_
        parameters = {"target": "identity", "intensity": float(estimator.shrinkage_), "ddof": 0}
    elif method == "nonlinear":
        matrix = nonlinear_covariance(values)
        parameters = {
            "effective_observations": len(values) - 1,
            "bandwidth": (len(values) - 1) ** (-1 / 3),
        }
    elif method == "cv_linear":
        parameters = fixed_parameters or select_shrinkage(values, folds, target)
        matrix = linear_covariance(values, parameters["intensity"], parameters["target"])
    else:
        raise AnalyticsError(f"Unknown covariance method: {method}.")
    matrix, diagnostics = validate_covariance(matrix)
    return CovarianceEstimate(matrix, method, diagnostics, parameters)
