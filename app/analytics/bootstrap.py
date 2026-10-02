"""Politis-Romano stationary bootstrap; complete monthly vectors are resampled jointly."""

import hashlib
import time
from collections import Counter

import numpy as np

from app.analytics.estimators import estimate_covariance
from app.analytics.frontier import data_diagnostics, describe, prepare
from app.analytics.metrics import portfolio_metrics
from app.analytics.models import AnalyticsError, OptimizationResult, ReturnPanel
from app.analytics.optimizer import PortfolioOptimizer


def stationary_indices(observations: int, expected_block_length: float, rng) -> np.ndarray:
    if observations < 1 or not 3 <= expected_block_length <= 24:
        raise AnalyticsError("Expected bootstrap block length must be 3 to 24 months.")
    indices = np.empty(observations, dtype=np.int64)
    position = 0
    while position < observations:
        start = int(rng.integers(observations))
        length = min(int(rng.geometric(1 / expected_block_length)), observations - position)
        indices[position : position + length] = (start + np.arange(length)) % observations
        position += length
    return indices


def bootstrap_weight_statistics(weights: np.ndarray, original: np.ndarray, constraints) -> dict:
    if weights.ndim != 2 or len(weights) == 0:
        raise AnalyticsError("Weight statistics require at least one successful iteration.")
    by_asset = {}
    for i, asset in enumerate(constraints.assets):
        column = weights[:, i]
        by_asset[asset] = {
            "original": float(original[i]),
            "mean": float(column.mean()),
            "median": float(np.median(column)),
            "standard_deviation": float(column.std(ddof=0)),
            "p5": float(np.quantile(column, 0.05)),
            "p95": float(np.quantile(column, 0.95)),
            "minimum": float(column.min()),
            "maximum": float(column.max()),
            "lower_bound_frequency": float(np.mean(np.abs(column - constraints.lower[i]) <= 1e-6)),
            "upper_bound_frequency": float(np.mean(np.abs(column - constraints.upper[i]) <= 1e-6)),
        }
    distances = np.abs(weights - original).sum(1) / 2
    return {
        "assets": by_asset,
        "allocation_distance": {
            "mean": float(distances.mean()),
            "median": float(np.median(distances)),
            "p5": float(np.quantile(distances, 0.05)),
            "p95": float(np.quantile(distances, 0.95)),
        },
    }


def _validate_settings(iterations: int, block_length: float, seed: int) -> None:
    if not isinstance(iterations, int) or not 1 <= iterations <= 2000:
        raise AnalyticsError("Bootstrap iterations must be an integer from 1 to 2,000.")
    if not 3 <= block_length <= 24:
        raise AnalyticsError("Expected bootstrap block length must be 3 to 24 months.")
    if not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise AnalyticsError("The random seed must be an integer from 0 to 2^32-1.")


def _sample_estimate(values: np.ndarray, estimate, options: dict):
    return estimate_covariance(
        values,
        estimate.method,
        folds=options.get("cv_folds", 5),
        target=options.get("shrinkage_target", "identity"),
        fixed_parameters=estimate.parameters if estimate.method == "cv_linear" else None,
    )


def _summary(records: list[dict], requested: int) -> dict:
    successful = sum(record["status"] == "optimal" for record in records)
    failures = [record for record in records if record["status"] != "optimal"]
    return {
        "requested_iterations": requested,
        "successful_iterations": successful,
        "failed_iterations": requested - successful,
        "status": "complete" if successful / requested >= 0.95 else "incomplete",
        "failure_reasons": dict(Counter(r.get("reason", r["status"]) for r in failures)),
        "failures": failures,
    }


def analyze_bootstrap(
    panel: ReturnPanel,
    *,
    bootstrap_iterations: int = 1000,
    expected_block_length: float = 12,
    random_seed: int = 42,
    objectives: list[str] | None = None,
    risk_free_rate: float = 0.03,
    **options,
) -> dict:
    _validate_settings(bootstrap_iterations, expected_block_length, random_seed)
    objectives = objectives or ["gmv", "max_sharpe"]
    if len(set(objectives)) != len(objectives) or not set(objectives) <= {"gmv", "max_sharpe"}:
        raise AnalyticsError(
            "Initial bootstrap supports distinct GMV and Max-Sharpe objectives only."
        )
    started = time.perf_counter()
    constraints, estimate, mean, covariance = prepare(panel, **options)
    engine = PortfolioOptimizer(mean, covariance, constraints)
    originals = {objective: engine.optimize(objective, risk_free_rate) for objective in objectives}
    records = {objective: [] for objective in objectives}
    weight_vectors = {objective: [] for objective in objectives}
    rng, fingerprint = np.random.default_rng(random_seed), hashlib.sha256()
    for iteration in range(bootstrap_iterations):
        indices = stationary_indices(len(panel.values), expected_block_length, rng)
        fingerprint.update(indices.astype("<i8").tobytes())
        values = panel.values[indices]
        try:
            sample_estimate = _sample_estimate(values, estimate, options)
            sample_mean, sample_covariance = values.mean(0) * 12, sample_estimate.matrix * 12
            sample_engine = PortfolioOptimizer(sample_mean, sample_covariance, constraints)
        except (AnalyticsError, np.linalg.LinAlgError) as exc:
            for objective in objectives:
                records[objective].append(
                    {
                        "iteration": iteration,
                        "status": "estimator_failed",
                        "reason": str(exc),
                        "weights": None,
                    }
                )
            continue
        for objective in objectives:
            result = sample_engine.optimize(objective, risk_free_rate)
            record = {
                "iteration": iteration,
                "status": result.status,
                "diagnostics": result.diagnostics,
                "weights": None,
                "metrics": None,
                "covariance_diagnostics": sample_estimate.diagnostics,
            }
            if result.weights is not None:
                weight_vectors[objective].append(result.weights)
                record["weights"] = dict(zip(panel.assets, result.weights.tolist(), strict=True))
                record["metrics"] = portfolio_metrics(
                    result.weights, values, sample_mean, sample_covariance, risk_free_rate
                )
            records[objective].append(record)
    results = {}
    for objective in objectives:
        summary = _summary(records[objective], bootstrap_iterations)
        summary.update(
            original=describe(originals[objective], panel, mean, covariance, risk_free_rate),
            resampled=None,
            stability=None,
            iterations=records[objective],
        )
        # Do not offer a partial average as an accepted resampled result below the 95% gate.
        if summary["status"] == "complete" and originals[objective].weights is not None:
            weights = np.stack(weight_vectors[objective])
            average = weights.mean(0)
            constraints.require_feasible(average)
            summary["resampled"] = describe(
                OptimizationResult(
                    "averaged", average, {"constraint_violation": constraints.violation(average)}
                ),
                panel,
                mean,
                covariance,
                risk_free_rate,
            )
            summary["stability"] = bootstrap_weight_statistics(
                weights, originals[objective].weights, constraints
            )
        elif originals[objective].weights is None:
            summary["status"] = "incomplete"
        results[objective] = summary
    data = data_diagnostics(panel, estimate)
    return {
        **data,
        "status": "complete"
        if all(r["status"] == "complete" for r in results.values())
        else "incomplete",
        "assets": panel.assets,
        "currency": "USD",
        "risk_free_rate": risk_free_rate,
        "methodology": "stationary_bootstrap",
        "expected_block_length": expected_block_length,
        "restart_probability": 1 / expected_block_length,
        "random_seed": random_seed,
        "sample_length": len(panel.values),
        "sample_fingerprint": fingerprint.hexdigest(),
        "conditional_bootstrap": estimate.method == "cv_linear",
        "hyperparameter_uncertainty_included": estimate.method != "cv_linear",
        "covariance_method": estimate.method,
        "covariance_parameters": estimate.parameters,
        "covariance_diagnostics": estimate.diagnostics,
        "source_metadata": panel.metadata,
        "warnings": data["warnings"]
        + (
            [
                "Conditional bootstrap holds original shrinkage target and "
                "intensity fixed; hyperparameter-selection uncertainty is excluded."
            ]
            if estimate.method == "cv_linear"
            else []
        ),
        "interpretation": "Allocation sensitivity; not guaranteed future-return intervals.",
        "portfolio_evaluation": "original_and_resampled_on_same_original_full_sample",
        "objectives": results,
        "execution_seconds": time.perf_counter() - started,
    }


def analyze_resampled_frontier(
    panel: ReturnPanel,
    *,
    frontier_points: int = 51,
    bootstrap_iterations: int = 250,
    expected_block_length: float = 12,
    random_seed: int = 42,
    risk_aversions: list[float] | None = None,
    risk_free_rate: float = 0.03,
    **options,
) -> dict:
    _validate_settings(bootstrap_iterations, expected_block_length, random_seed)
    if not 2 <= frontier_points <= 201:
        raise AnalyticsError("Resampled frontier points must be from 2 to 201.")
    aversions = np.asarray(
        risk_aversions if risk_aversions is not None else np.geomspace(1e4, 1e-3, frontier_points),
        dtype=float,
    )
    if (
        len(aversions) != frontier_points
        or not np.isfinite(aversions).all()
        or np.any(aversions <= 0)
        or np.any(aversions > 1e6)
    ):
        raise AnalyticsError("Supply one finite risk aversion in (0, 1e6] per frontier point.")
    started = time.perf_counter()
    constraints, estimate, mean, covariance = prepare(panel, **options)
    rng, fingerprint = np.random.default_rng(random_seed), hashlib.sha256()
    weights = [[] for _ in aversions]
    records, diagnostics = [], []
    for iteration in range(bootstrap_iterations):
        indices = stationary_indices(len(panel.values), expected_block_length, rng)
        fingerprint.update(indices.astype("<i8").tobytes())
        values = panel.values[indices]
        try:
            sample = _sample_estimate(values, estimate, options)
            engine = PortfolioOptimizer(values.mean(0) * 12, sample.matrix * 12, constraints)
            solutions = [engine.utility(1 / float(gamma)) for gamma in aversions]
            diagnostics.append(
                {
                    "iteration": iteration,
                    "covariance": sample.diagnostics,
                    "solver_results": [r.diagnostics for r in solutions],
                }
            )
            failed = [i for i, result in enumerate(solutions) if result.weights is None]
            if failed:
                records.append(
                    {
                        "iteration": iteration,
                        "status": "optimization_failed",
                        "failed_points": failed,
                        "reason": "; ".join(solutions[i].status for i in failed),
                    }
                )
                continue
            # Count a bootstrap sample as successful only if every shared gamma succeeded.
            for point, result in enumerate(solutions):
                weights[point].append(result.weights)
            records.append({"iteration": iteration, "status": "optimal"})
        except (AnalyticsError, np.linalg.LinAlgError) as exc:
            records.append(
                {"iteration": iteration, "status": "estimator_failed", "reason": str(exc)}
            )
    summary = _summary(records, bootstrap_iterations)
    points = []
    if summary["status"] == "complete":
        for gamma, vectors in zip(aversions, weights, strict=True):
            average = np.mean(vectors, axis=0)
            constraints.require_feasible(average)
            point = describe(
                OptimizationResult(
                    "averaged", average, {"constraint_violation": constraints.violation(average)}
                ),
                panel,
                mean,
                covariance,
                risk_free_rate,
            )
            point["risk_aversion"] = float(gamma)
            points.append(point)
    data = data_diagnostics(panel, estimate)
    return {
        **data,
        **summary,
        "assets": panel.assets,
        "currency": "USD",
        "risk_free_rate": risk_free_rate,
        "methodology": "stationary_bootstrap_shared_risk_aversion_weight_averaging",
        "label": "Resampled frontier (not the exact classical efficient frontier)",
        "risk_aversions": aversions.tolist(),
        "frontier": points,
        "expected_block_length": expected_block_length,
        "random_seed": random_seed,
        "sample_fingerprint": fingerprint.hexdigest(),
        "sample_length": len(panel.values),
        "conditional_bootstrap": estimate.method == "cv_linear",
        "covariance_method": estimate.method,
        "covariance_parameters": estimate.parameters,
        "covariance_diagnostics": estimate.diagnostics,
        "iteration_diagnostics": diagnostics,
        "evaluation": "averaged_weights_evaluated_on_original_full_sample",
        "source_metadata": panel.metadata,
        "warnings": data["warnings"]
        + (
            ["Conditional bootstrap excludes shrinkage hyperparameter-selection uncertainty."]
            if estimate.method == "cv_linear"
            else []
        ),
        "execution_seconds": time.perf_counter() - started,
    }
