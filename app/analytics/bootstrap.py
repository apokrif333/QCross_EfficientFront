"""Politis-Romano stationary bootstrap; complete monthly vectors are resampled jointly."""

import hashlib
import time
from collections import Counter

import numpy as np

from app.analytics.bootstrap_metrics import fixed_portfolio_metrics, summarize_bootstrap_portfolio
from app.analytics.constraints import user_allocation
from app.analytics.efficiency_distance import efficiency_distance, summarize_distances
from app.analytics.estimators import estimate_covariance
from app.analytics.frontier import data_diagnostics, describe, prepare
from app.analytics.metrics import portfolio_metrics
from app.analytics.models import AnalyticsError, OptimizationResult, ReturnPanel
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.paired_comparison import PairedBootstrap
from app.analytics.provenance import reproducible
from app.analytics.references import differential_moments


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


def bootstrap_weight_statistics(
    weights: np.ndarray, original: np.ndarray | None, constraints
) -> dict:
    if weights.ndim != 2 or len(weights) == 0:
        raise AnalyticsError("Weight statistics require at least one successful iteration.")
    by_asset = {}
    for i, asset in enumerate(constraints.assets):
        column = weights[:, i]
        by_asset[asset] = {
            "original": float(original[i]) if original is not None else None,
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
    distances = np.abs(weights - original).sum(1) / 2 if original is not None else None
    return {
        "assets": by_asset,
        "allocation_distance": {
            "mean": float(distances.mean()),
            "median": float(np.median(distances)),
            "p5": float(np.quantile(distances, 0.05)),
            "p95": float(np.quantile(distances, 0.95)),
        }
        if distances is not None
        else None,
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
        folds=options.get("cv_folds", 4),
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


TARGET_TOLERANCE = 1e-7


def _target_solution(engine, target, risk_free_rate):
    maximum = engine.maximum_return()
    diagnostics = {
        "feasibility_solver": maximum.diagnostics,
        "target_return": target,
        "target_tolerance": TARGET_TOLERANCE,
        "target_feasible": None,
    }
    if maximum.status != "optimal" or maximum.weights is None:
        return OptimizationResult("solver_failed", None, diagnostics)
    maximum_return = float(engine.mean @ maximum.weights)
    feasible = maximum_return >= target - TARGET_TOLERANCE
    diagnostics.update(maximum_achievable_return=maximum_return, target_feasible=feasible)
    if not feasible:
        return OptimizationResult("target_infeasible", None, diagnostics)
    # At the feasibility boundary, permit only the disclosed annual-return tolerance.
    effective_target = min(target, maximum_return)
    diagnostics["effective_target_return"] = effective_target
    result = engine.optimize("target_return", risk_free_rate, effective_target)
    result.diagnostics.update(diagnostics)
    if result.status != "optimal" or result.weights is None:
        return OptimizationResult(
            "solver_failed", None, {**result.diagnostics, "target_solver_status": result.status}
        )
    return result


@reproducible
def analyze_bootstrap(
    panel: ReturnPanel,
    *,
    bootstrap_iterations: int = 1000,
    expected_block_length: float = 12,
    random_seed: int = 42,
    objectives: list[str] | None = None,
    risk_free_rate: float = 0.03,
    target_return: float | None = None,
    user_weights: dict | None = None,
    include_efficiency_robustness: bool = True,
    include_paired_comparison: bool = True,
    **options,
) -> dict:
    _validate_settings(bootstrap_iterations, expected_block_length, random_seed)
    objectives = objectives or ["gmv", "max_sharpe"]
    if len(set(objectives)) != len(objectives) or not set(objectives) <= {
        "gmv",
        "max_sharpe",
        "target_return",
        "max_return_at_user_risk",
    }:
        raise AnalyticsError(
            "Bootstrap supports distinct GMV, Max Sharpe and target_return objectives."
        )
    if "target_return" in objectives and (target_return is None or not np.isfinite(target_return)):
        raise AnalyticsError("Target Return Bootstrap requires a finite annual target_return.")
    if user_weights and include_paired_comparison and bootstrap_iterations % 2:
        raise AnalyticsError("Paired Bootstrap requires an even number of generated datasets.")
    started = time.perf_counter()
    constraints, estimate, mean, covariance = prepare(panel, **options)
    fixed_user_weights = user_allocation(panel.assets, user_weights) if user_weights else None
    paired = (
        PairedBootstrap(panel.assets, fixed_user_weights, constraints, **panel.simulation_options)
        if fixed_user_weights is not None and include_paired_comparison
        else None
    )
    engine = PortfolioOptimizer(mean, covariance, constraints, **panel.differential)
    originals = {
        objective: _target_solution(engine, target_return, risk_free_rate)
        if objective == "target_return"
        else engine.optimize(objective, risk_free_rate, user_weights=fixed_user_weights)
        for objective in objectives
    }
    records = {objective: [] for objective in objectives}
    weight_vectors = {objective: [] for objective in objectives}
    rng, fingerprint = np.random.default_rng(random_seed), hashlib.sha256()
    efficiency_records = []
    control_samples = []
    for iteration in range(bootstrap_iterations):
        indices = stationary_indices(len(panel.values), expected_block_length, rng)
        fingerprint.update(indices.astype("<i8").tobytes())
        values = panel.values[indices]
        if panel.control_config.get("mode", "none") != "none":
            from app.analytics.portfolio_controls import compare_controls

            control_samples.append(
                compare_controls(panel, user_weights, indices, record_history=False)
            )
        sample_rf = (
            panel.risk_free_returns[indices] if panel.risk_free_returns is not None else None
        )
        fixed_metrics_record = {}
        if fixed_user_weights is not None:
            try:
                metrics = fixed_portfolio_metrics(
                    values,
                    fixed_user_weights,
                    sample_rf
                    if sample_rf is not None
                    else np.full(len(values), (1 + risk_free_rate) ** (1 / 12) - 1),
                    **panel.simulation_options,
                )
                fixed_metrics_record = {
                    "observations": len(values),
                    "portfolio_metrics": metrics,
                    "portfolio_metrics_diagnostics": {
                        "unavailable_metrics": [
                            key for key, value in metrics.items() if value is None
                        ],
                    },
                }
            except Exception as exc:  # Optional diagnostics cannot change optimizer outcomes.
                fixed_metrics_record = {
                    "observations": len(values),
                    "portfolio_metrics": {},
                    "portfolio_metrics_diagnostics": {"reason": str(exc)},
                }
        paired_consumed = False
        try:
            sample_estimate = _sample_estimate(values, estimate, options)
            if paired is not None:
                paired.consume(
                    iteration,
                    values,
                    sample_rf
                    if sample_rf is not None
                    else np.full(len(values), (1 + risk_free_rate) ** (1 / 12) - 1),
                    sample_estimate,
                )
                paired_consumed = True
            sample_mean, sample_covariance = values.mean(0) * 12, sample_estimate.matrix * 12
            if fixed_user_weights is not None:
                try:
                    distance = efficiency_distance(
                        values,
                        sample_estimate.matrix,
                        fixed_user_weights,
                        constraints,
                        estimator_parameters=sample_estimate.parameters,
                        covariance_diagnostics=sample_estimate.diagnostics,
                    )
                    diagnostics = {
                        key: value
                        for key, value in distance["diagnostics"].items()
                        if key not in {"covariance_diagnostics", "estimator_parameters"}
                    }
                    efficiency_records.append(
                        {
                            "iteration": iteration,
                            **fixed_metrics_record,
                            "status": distance["status"],
                            "user_expected_return": distance.get("user_expected_return"),
                            "user_volatility": distance.get("user_volatility"),
                            "return_shortfall": distance["return_shortfall"],
                            "excess_volatility": distance["excess_volatility"],
                            "diagnostics": diagnostics,
                        }
                    )
                except Exception as exc:  # Optional diagnostics cannot change optimizer outcomes.
                    efficiency_records.append(
                        {
                            "iteration": iteration,
                            **fixed_metrics_record,
                            "status": "failed",
                            "diagnostics": {"reason": str(exc)},
                        }
                    )
            differential = differential_moments(
                values,
                sample_rf,
                estimate.method,
                folds=options.get("cv_folds", 4),
                target=options.get("shrinkage_target", "identity"),
                fixed_parameters=panel.differential["differential_estimate"].parameters
                if estimate.method == "cv_linear" and panel.differential
                else None,
            )
            sample_engine = PortfolioOptimizer(
                sample_mean, sample_covariance, constraints, **differential
            )
        except (AnalyticsError, np.linalg.LinAlgError) as exc:
            if paired is not None and not paired_consumed:
                paired.consume(
                    iteration,
                    values,
                    sample_rf
                    if sample_rf is not None
                    else np.full(len(values), (1 + risk_free_rate) ** (1 / 12) - 1),
                    None,
                )
            if fixed_user_weights is not None and len(efficiency_records) == iteration:
                efficiency_records.append(
                    {
                        "iteration": iteration,
                        **fixed_metrics_record,
                        "status": "failed",
                        "diagnostics": {"reason": str(exc)},
                    }
                )
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
            result = (
                _target_solution(sample_engine, target_return, risk_free_rate)
                if objective == "target_return"
                else sample_engine.optimize(
                    objective, risk_free_rate, user_weights=fixed_user_weights
                )
            )
            record = {
                "iteration": iteration,
                "status": result.status,
                **(
                    {"target_feasible": result.diagnostics.get("target_feasible")}
                    if objective == "target_return"
                    else {}
                ),
                "diagnostics": result.diagnostics,
                "weights": None,
                "metrics": None,
                "covariance_diagnostics": sample_estimate.diagnostics,
                "differential_covariance_parameters": differential[
                    "differential_estimate"
                ].parameters
                if differential
                else {},
                "differential_covariance_diagnostics": differential[
                    "differential_estimate"
                ].diagnostics
                if differential
                else {},
            }
            if result.weights is not None:
                weight_vectors[objective].append(result.weights)
                record["weights"] = dict(zip(panel.assets, result.weights.tolist(), strict=True))
                record["metrics"] = portfolio_metrics(
                    result.weights,
                    values,
                    sample_mean,
                    sample_covariance,
                    risk_free_rate,
                    risk_free_returns=sample_rf,
                    differential=differential,
                    simulation_options=panel.simulation_options,
                )
            records[objective].append(record)
    results = {}
    for objective in objectives:
        summary = _summary(records[objective], bootstrap_iterations)
        if objective == "target_return":
            technical = [
                r for r in records[objective] if r["status"] not in {"optimal", "target_infeasible"}
            ]
            feasible = sum(r.get("target_feasible") is True for r in records[objective])
            summary.update(
                status="complete"
                if not technical and originals[objective].status != "solver_failed"
                else "incomplete",
                computational_status="complete"
                if not technical and originals[objective].status != "solver_failed"
                else "incomplete",
                target_return=target_return,
                target_feasibility_rate=feasible / bootstrap_iterations,
                target_feasible_iterations=feasible,
                target_infeasible_iterations=sum(
                    r["status"] == "target_infeasible" for r in records[objective]
                ),
                numerical_failure_iterations=len(technical),
                feasibility_unknown_iterations=sum(
                    r.get("target_feasible") is None for r in records[objective]
                ),
                failed_iterations=len(technical),
                failures=technical,
                failure_reasons=dict(Counter(r["status"] for r in technical)),
                statistics_condition="successful_target_feasible_samples",
                averaging_policy="no_aggregate_if_any_numerical_failure",
                target_interpretation=(
                    "Feasibility across historical resamples, not probability of future returns."
                ),
                original_target_feasible=originals[objective].diagnostics.get("target_feasible"),
                resampled_target_evaluation=None,
            )
        summary.update(
            original=describe(originals[objective], panel, mean, covariance, risk_free_rate),
            resampled=None,
            stability=None,
            iterations=records[objective],
        )
        # GMV/Sharpe retain their 95% gate. Target outcomes need no technical failures;
        # economically infeasible samples are excluded from conditional statistics.
        if (
            summary["status"] == "complete"
            and weight_vectors[objective]
            and (originals[objective].weights is not None or objective == "target_return")
        ):
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
            if objective == "target_return":
                actual_return = float(average @ mean)
                summary["resampled_target_evaluation"] = {
                    "expected_return": actual_return,
                    "target_return": target_return,
                    "target_shortfall": max(0.0, target_return - actual_return),
                    "target_surplus": max(0.0, actual_return - target_return),
                    "meets_target": actual_return >= target_return - TARGET_TOLERANCE,
                    "evaluation": "original_full_sample_estimates",
                    "target_guaranteed": False,
                }
        elif originals[objective].weights is None and objective != "target_return":
            summary["status"] = "incomplete"
        results[objective] = summary
    data = data_diagnostics(panel, estimate, risk_free_rate)
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
        "portfolio_control_samples": control_samples,
        "paired_comparison": paired.result(len(panel.values)) if paired is not None else None,
        "efficiency_robustness": {
            "user_weights": dict(zip(panel.assets, fixed_user_weights.tolist(), strict=True)),
            "evaluation": "stationary_bootstrap_resampled_scenarios_not_future_probability",
            **summarize_distances(efficiency_records),
            **summarize_bootstrap_portfolio(efficiency_records, len(panel.values)),
        }
        if fixed_user_weights is not None
        else None,
        "execution_seconds": time.perf_counter() - started,
    }


@reproducible
def analyze_resampled_frontier(
    panel: ReturnPanel,
    *,
    frontier_points: int = 51,
    bootstrap_iterations: int = 250,
    expected_block_length: float = 12,
    random_seed: int = 42,
    risk_aversions: list[float] | None = None,
    risk_free_rate: float = 0.03,
    objective: str | None = None,
    target_return: float | None = None,
    user_weights: dict | None = None,
    **options,
) -> dict:
    # Global portfolio controls do not define this shared risk-aversion curve.
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
    fixed_user_weights = user_allocation(panel.assets, user_weights)
    rng, fingerprint = np.random.default_rng(random_seed), hashlib.sha256()
    weights = [[] for _ in aversions]
    records, diagnostics, user_sample_moments = [], [], []
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
            if fixed_user_weights is not None:
                user_sample_moments.append(
                    (
                        float(values.mean(0) @ fixed_user_weights * 12),
                        float(
                            np.sqrt(
                                max(0, fixed_user_weights @ sample.matrix @ fixed_user_weights * 12)
                            )
                        ),
                    )
                )
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
    data = data_diagnostics(panel, estimate, risk_free_rate)
    user_portfolio = (
        describe(
            OptimizationResult("reference", fixed_user_weights, {}),
            panel,
            mean,
            covariance,
            risk_free_rate,
        )
        if fixed_user_weights is not None
        else None
    )
    user_sample_average = None
    if user_sample_moments:
        average_moments = np.mean(user_sample_moments, axis=0)
        user_sample_average = {
            "expected_return": float(average_moments[0]),
            "volatility": float(average_moments[1]),
            "successful_samples": len(user_sample_moments),
        }
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
        "user_portfolio": user_portfolio,
        "user_sample_average": user_sample_average,
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
