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
    _sample_indices: list[np.ndarray] | None = None,
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
    controls_enabled = panel.control_config.get("mode", "none") != "none"
    paired = (
        PairedBootstrap(
            panel.assets,
            fixed_user_weights,
            constraints,
            controls_enabled=controls_enabled,
            **panel.simulation_options,
        )
        if fixed_user_weights is not None and include_paired_comparison
        else None
    )
    engine = PortfolioOptimizer(mean, covariance, constraints, **panel.differential)
    original_user_risk = None
    if controls_enabled and "max_return_at_user_risk" in objectives:
        from app.analytics.portfolio_controls import compare_controls

        original_user_risk = compare_controls(panel, user_weights, record_history=False)[
            "controlled"
        ]["net"]["annualized_volatility"]
    originals = {
        objective: _target_solution(engine, target_return, risk_free_rate)
        if objective == "target_return"
        else engine.optimize(
            objective,
            risk_free_rate,
            user_weights=fixed_user_weights,
            user_risk_limit=original_user_risk,
        )
        for objective in objectives
    }
    records = {objective: [] for objective in objectives}
    weight_vectors = {objective: [] for objective in objectives}
    rng, fingerprint = np.random.default_rng(random_seed), hashlib.sha256()
    efficiency_records = []
    control_samples = []
    for iteration in range(bootstrap_iterations):
        indices = stationary_indices(len(panel.values), expected_block_length, rng)
        if _sample_indices is not None:
            # ReturnPanel supports at most 10,000 months.
            _sample_indices.append(indices.astype(np.uint16))
        fingerprint.update(indices.astype("<i8").tobytes())
        values = panel.values[indices]
        control_sample = None
        if controls_enabled:
            from app.analytics.control_cache import indices_hash
            from app.analytics.portfolio_controls import compare_controls

            try:
                control_sample = compare_controls(
                    panel,
                    user_weights,
                    indices,
                    record_history=False,
                    include_wealth=paired is not None and iteration % 2 == 1,
                )
            except Exception as exc:
                control_sample = {"status": "failed", "reason": str(exc)}
            control_sample["indices_sha256"] = indices_hash(indices)
            control_samples.append(control_sample)
        sample_rf = (
            panel.risk_free_returns[indices] if panel.risk_free_returns is not None else None
        )
        fixed_metrics_record = {}
        if fixed_user_weights is not None:
            try:
                metrics = (
                    fixed_portfolio_metrics(
                        values,
                        fixed_user_weights,
                        sample_rf
                        if sample_rf is not None
                        else np.full(len(values), (1 + risk_free_rate) ** (1 / 12) - 1),
                        **panel.simulation_options,
                    )
                    if not controls_enabled
                    else {
                        **control_sample["controlled"]["net"],
                        "gross_metrics": control_sample["controlled"]["gross"],
                    }
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
                    control_sample=control_sample,
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
                    control_sample=control_sample,
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
            if (
                objective == "max_return_at_user_risk"
                and controls_enabled
                and (not control_sample or "controlled" not in control_sample)
            ):
                result = OptimizationResult(
                    "control_failed",
                    None,
                    {"reason": (control_sample or {}).get("reason", "missing simulation")},
                )
            else:
                result = (
                    _target_solution(sample_engine, target_return, risk_free_rate)
                    if objective == "target_return"
                    else sample_engine.optimize(
                        objective,
                        risk_free_rate,
                        user_weights=fixed_user_weights,
                        user_risk_limit=control_sample["controlled"]["net"]["annualized_volatility"]
                        if controls_enabled and objective == "max_return_at_user_risk"
                        else None,
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
            "user_portfolio_mode": "controlled" if controls_enabled else "passive",
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
    bootstrap_control_cache: dict | None = None,
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
    controls_enabled = panel.control_config.get("mode", "none") != "none"
    if controls_enabled:
        from app.analytics.portfolio_controls import compare_controls
    from app.analytics.control_cache import (
        control_cache_key,
        indices_hash,
        usable_original,
        usable_sample,
    )

    cache = bootstrap_control_cache or {}
    cache_matches = bool(
        controls_enabled
        and cache.get("version") == 1
        and cache.get("key")
        == control_cache_key(panel, user_weights, expected_block_length, random_seed)
        and usable_original(cache.get("original", {}).get("controlled"))
        and usable_original(cache.get("original", {}).get("baseline"))
    )
    cached_samples = cache.get("samples", []) if cache_matches else []
    reused_samples = computed_samples = 0

    control_failures = []
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
                if controls_enabled:
                    try:
                        saved = (
                            cached_samples[iteration] if iteration < len(cached_samples) else None
                        )
                        if usable_sample(saved) and saved.get("indices_sha256") == indices_hash(
                            indices
                        ):
                            reused_samples += 1
                            if saved.get("error"):
                                raise AnalyticsError(saved["error"])
                            user_sample_moments.append(
                                (saved["expected_return"], saved["volatility"])
                            )
                        else:
                            computed_samples += 1
                            path = compare_controls(
                                panel, user_weights, indices, record_history=False
                            )["controlled"]["net"]
                            user_sample_moments.append(
                                (
                                    path["annualized_arithmetic_return"],
                                    path["annualized_volatility"],
                                )
                            )
                    except Exception as exc:
                        control_failures.append({"iteration": iteration, "reason": str(exc)})
                else:
                    user_sample_moments.append(
                        (
                            float(values.mean(0) @ fixed_user_weights * 12),
                            float(
                                np.sqrt(
                                    max(
                                        0,
                                        fixed_user_weights
                                        @ sample.matrix
                                        @ fixed_user_weights
                                        * 12,
                                    )
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
    control_result = None
    if controls_enabled and fixed_user_weights is not None:
        control_result = (
            cache["original"] if cache_matches else compare_controls(panel, user_weights)
        )
        path = control_result["controlled"]
        net = path["net"]
        control_result["months"] = [str(m) for m in panel.returns.index]
        control_result["source_metadata"] = panel.control_metadata
        user_portfolio = {
            "status": "historical_path",
            "weights": dict(zip(panel.assets, fixed_user_weights.tolist(), strict=True)),
            "diagnostics": {
                "user_portfolio_mode": "controlled",
                "evaluation": "net_historical_path",
            },
            "metrics": {
                "expected_return": net["annualized_arithmetic_return"],
                "volatility": net["annualized_volatility"],
                "historical_arithmetic_return": net["annualized_arithmetic_return"],
                "historical_volatility": net["annualized_volatility"],
                "historical_cagr": net["annualized_cagr"],
                "historical_sharpe_ratio": net["historical_sharpe_ratio"],
                "sharpe_ratio": None,
                "historical_max_drawdown": net["maximum_drawdown"],
                "terminal_wealth": net["terminal_wealth"],
                "gross_historical_metrics": path["gross"],
                "trading_costs": {
                    k: path[k]
                    for k in (
                        "total_commissions",
                        "trade_count",
                        "rebalancing_events",
                        "annualized_turnover",
                    )
                },
            },
        }
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
        "user_portfolio_mode": "controlled" if controls_enabled else "passive",
        "portfolio_controls": control_result,
        "user_sample_diagnostics": {
            "cache_status": "matched" if cache_matches else "incompatible" if cache else "absent",
            "reused_samples": reused_samples,
            "computed_samples": computed_samples,
            "successful_samples": len(user_sample_moments),
            "failed_control_samples": len(control_failures),
            "failures": control_failures,
            "excluded_frontier_samples": summary["failed_iterations"],
        },
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
