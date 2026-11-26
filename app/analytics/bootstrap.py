"""Politis-Romano stationary bootstrap; complete monthly vectors are resampled jointly."""

import hashlib
import time
from collections import Counter

import numpy as np

from app.analytics.bootstrap_metrics import (
    fixed_portfolio_metrics,
    simulation_metrics,
    summarize_bootstrap_portfolio,
)
from app.analytics.constraints import user_allocation
from app.analytics.control_cache import indices_hash
from app.analytics.cross_validation import period_description
from app.analytics.efficiency_distance import efficiency_distance, summarize_distances
from app.analytics.estimators import estimate_covariance
from app.analytics.frontier import data_diagnostics, describe, prepare
from app.analytics.models import TAX_METHODOLOGY, AnalyticsError, OptimizationResult, ReturnPanel
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.paired_comparison import (
    JOINED_METRICS,
    joined_validation_comparison,
    summarize,
    validation_frontier,
)
from app.analytics.portfolio_controls import compare_controls
from app.analytics.portfolio_simulator import TRADING_METRICS
from app.analytics.provenance import reproducible
from app.analytics.references import differential_moments
from app.analytics.splitting import BOOTSTRAP_METHODOLOGY, bootstrap_folds


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


def bootstrap_generation(splits, block_length, seed, iteration):
    """Replay one generation from its zero-based identifier and the original folds."""
    for fold, (train, validation) in enumerate(splits):
        train_rng, valid_rng = (
            np.random.Generator(
                np.random.PCG64(np.random.SeedSequence([seed, iteration, fold, side]))
            )
            for side in (0, 1)
        )
        yield (
            train[stationary_indices(len(train), block_length, train_rng)],
            validation[stationary_indices(len(validation), block_length, valid_rng)],
        )


def _compact_parameters(parameters):
    # Full inner search grids can be replayed from the stored fold seed and bounds.
    return {k: v for k, v in parameters.items() if k not in {"grid", "nll_scores", "split_periods"}}


def _fit_bootstrap_fold(
    panel,
    train,
    rf,
    constraints,
    objectives,
    user_weights,
    fixed_weights,
    risk_free_rate,
    target_return,
    method,
    folds,
    controls_enabled,
    calendar_start,
):
    values = panel.values[train]
    settings = {"folds": folds, "target": "auto" if method == "cv_linear" else "identity"}
    estimate = estimate_covariance(values, method, **settings)
    training_rf = rf[train]
    if np.all(training_rf == training_rf[0]) and np.any(values != training_rf[0], axis=0).all():
        # Subtracting a constant affects only the mean, including inner CV likelihoods.
        differential = {
            "differential_mean": (values.mean(0) - training_rf[0]) * 12,
            "differential_covariance": estimate.matrix * 12,
            "differential_estimate": estimate,
        }
    else:
        differential = differential_moments(values, training_rf, method, **settings)
    engine = PortfolioOptimizer(
        values.mean(0) * 12, estimate.matrix * 12, constraints, **differential
    )
    covariance = {
        "covariance_parameters": _compact_parameters(estimate.parameters),
        "covariance_diagnostics": estimate.diagnostics,
        "differential_covariance_parameters": (
            _compact_parameters(differential["differential_estimate"].parameters)
            if differential
            else {}
        ),
    }
    risk_limit = None
    control_error = None
    if controls_enabled and "max_return_at_user_risk" in objectives:
        try:
            risk_limit = compare_controls(
                panel,
                user_weights,
                train,
                record_history=False,
                sampled_risk_free=training_rf,
                synthetic=True,
                calendar_start=calendar_start,
            )["controlled"]["net"]["annualized_volatility"]
        except (AnalyticsError, np.linalg.LinAlgError) as exc:
            control_error = str(exc)
    solutions = {}
    for objective in objectives:
        try:
            if objective == "max_return_at_user_risk" and control_error is not None:
                result = OptimizationResult("control_failed", None, {"reason": control_error})
            elif objective == "target_return":
                result = _target_solution(engine, target_return, risk_free_rate)
            else:
                result = engine.optimize(
                    objective,
                    risk_free_rate,
                    user_weights=fixed_weights,
                    user_risk_limit=risk_limit,
                )
        except (AnalyticsError, np.linalg.LinAlgError) as exc:
            result = OptimizationResult("solver_failed", None, {"reason": str(exc)})
        solutions[objective] = result
    return solutions, covariance


def _validation_record(fits, offsets, values, rf, panel, indices, iteration, include_wealth):
    folds = [
        {
            "fold": fold,
            "status": fit.status,
            "weights": dict(zip(panel.assets, fit.weights.tolist(), strict=True))
            if fit.weights is not None
            else None,
            "diagnostics": fit.diagnostics,
        }
        for fold, fit in enumerate(fits, 1)
    ]
    record = {"iteration": iteration, "weights": None, "folds": folds, "metrics": None}
    failures = [fit for fit in fits if fit.status != "optimal" or fit.weights is None]
    if failures:
        status = next(
            (fit.status for fit in failures if fit.status != "target_infeasible"),
            "target_infeasible",
        )
        return {**record, "status": status, "reason": status}
    try:
        path = fixed_portfolio_metrics(
            values,
            fits[0].weights,
            rf,
            include_wealth=include_wealth,
            target_schedule=dict(zip(offsets, (fit.weights for fit in fits[1:]), strict=True)),
            **panel.path_options(indices, synthetic=True),
        )
        return {**record, "status": "optimal", "metrics": path}
    except (AnalyticsError, np.linalg.LinAlgError) as exc:
        return {**record, "status": "validation_failed", "reason": str(exc)}


def _bootstrap_objective_summary(objective, records, constraints, target_return):
    count = len(records)
    summary = _summary(records, count)
    successful = [row for row in records if row["status"] == "optimal"]
    summary.update(
        original={"status": "not_applicable", "weights": None, "metrics": None, "diagnostics": {}},
        resampled=None,
        stability=None,
        iterations=records,
    )
    if successful and (summary["status"] == "complete" or objective == "target_return"):
        weights = np.array(
            [
                [fold["weights"][asset] for asset in constraints.assets]
                for row in successful
                for fold in row["folds"]
            ]
        )
        summary["stability"] = bootstrap_weight_statistics(weights, None, constraints)
    if objective == "target_return":
        infeasible = sum(r["status"] == "target_infeasible" for r in records)
        technical = [r for r in records if r["status"] not in {"optimal", "target_infeasible"}]
        feasible = sum(
            all(f["diagnostics"].get("target_feasible") is True for f in row["folds"])
            for row in records
        )
        summary.update(
            status="incomplete" if technical else "complete",
            computational_status="incomplete" if technical else "complete",
            target_return=target_return,
            target_feasibility_rate=feasible / count,
            target_feasible_iterations=feasible,
            target_infeasible_iterations=infeasible,
            numerical_failure_iterations=len(technical),
            statistics_condition="successful_target_feasible_samples",
            feasibility_unknown_iterations=sum(
                not all(f["diagnostics"].get("target_feasible") is not None for f in row["folds"])
                and not any(f["diagnostics"].get("target_feasible") is False for f in row["folds"])
                for row in records
            ),
            failed_iterations=len(technical),
            failure_reasons=dict(Counter(r["status"] for r in technical)),
            failures=technical,
        )
        if technical:
            summary["stability"] = None
    return summary


@reproducible
def analyze_bootstrap(
    panel: ReturnPanel,
    *,
    bootstrap_iterations: int = 500,
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
        raise AnalyticsError("Invalid or duplicate Bootstrap objective.")
    if "target_return" in objectives and (target_return is None or not np.isfinite(target_return)):
        raise AnalyticsError("Target Return Bootstrap requires a finite annual target_return.")
    started = time.perf_counter()
    method = options.get("covariance_method", "sample")
    cv_folds = options.get("cv_folds", 4)
    observations = len(panel.values)
    splits = bootstrap_folds(observations, cv_folds, method)
    # Descriptive history only; no selected estimator or optimizer is fit on full history.
    constraints, descriptive, _, _ = prepare(panel, **{**options, "covariance_method": "sample"})
    fixed_weights = user_allocation(panel.assets, user_weights) if user_weights else None
    controls_enabled = panel.control_config.get("mode", "none") != "none"
    if fixed_weights is None and (controls_enabled or "max_return_at_user_risk" in objectives):
        raise AnalyticsError("Portfolio controls and My Portfolio require explicit user weights.")
    if "max_return_at_user_risk" in objectives:
        constraints.require_feasible(fixed_weights)
    compare_optimizer = include_paired_comparison and fixed_weights is not None
    comparison_objective = "max_return_at_user_risk"
    fitted = list(
        dict.fromkeys([*objectives, *([comparison_objective] if compare_optimizer else [])])
    )
    rf = panel.risk_free_returns
    if rf is None:
        rf = np.full(observations, (1 + risk_free_rate) ** (1 / 12) - 1)
    offsets = np.cumsum([len(valid) for _, valid in splits])[:-1].tolist()
    outer_folds = [
        {
            "fold": i,
            "train": period_description(panel, train),
            "validation": period_description(panel, valid),
            "train_start_index": int(train[0]),
            "validation_start_index": int(valid[0]),
        }
        for i, (train, valid) in enumerate(splits, 1)
    ]
    records = {objective: [] for objective in fitted}
    generations, control_samples, efficiency_records, comparisons, clouds = [], [], [], [], []
    fingerprint, validation_fingerprint = hashlib.sha256(), hashlib.sha256()
    validation_mean = np.zeros(len(panel.assets))
    validation_covariance = np.zeros((len(panel.assets), len(panel.assets)))

    for iteration in range(bootstrap_iterations):
        draws = list(bootstrap_generation(splits, expected_block_length, random_seed, iteration))
        valid = np.concatenate([indices for _, indices in draws])
        if _sample_indices is not None:
            _sample_indices.append(valid.astype(np.uint16))
        validation_fingerprint.update(valid.astype("<i8").tobytes())
        validation_hash = indices_hash(valid)
        values, valid_rf = panel.values[valid], rf[valid]
        fold_fits = {objective: [] for objective in fitted}
        fold_diagnostics = []
        for fold, (train, validation) in enumerate(draws, 1):
            fingerprint.update(train.astype("<i8").tobytes())
            fingerprint.update(validation.astype("<i8").tobytes())
            try:
                solutions, diagnostics = _fit_bootstrap_fold(
                    panel,
                    train,
                    rf,
                    constraints,
                    fitted,
                    user_weights,
                    fixed_weights,
                    risk_free_rate,
                    target_return,
                    method,
                    cv_folds,
                    controls_enabled,
                    int(splits[fold - 1][0][0]),
                )
            except (AnalyticsError, np.linalg.LinAlgError) as exc:
                diagnostics = {"reason": str(exc)}
                solutions = {
                    objective: OptimizationResult("estimator_failed", None, diagnostics)
                    for objective in fitted
                }
            for objective in fitted:
                fold_fits[objective].append(solutions[objective])
            fold_diagnostics.append(
                {
                    **diagnostics,
                    "fold": fold,
                    "train_sha256": indices_hash(train),
                    "validation_sha256": indices_hash(validation),
                    "train_length": len(train),
                    "validation_length": len(validation),
                    "train_unique": len(np.unique(train)),
                    "validation_unique": len(np.unique(validation)),
                    "shared_source_months": len(np.intersect1d(train, validation)),
                }
            )
        generations.append(
            {
                "iteration": iteration,
                "validation_sha256": validation_hash,
                "validation_unique": len(np.unique(valid)),
                "folds": fold_diagnostics,
            }
        )

        user_metrics = None
        control_sample = None
        if fixed_weights is not None:
            try:
                if controls_enabled:
                    control_sample = compare_controls(
                        panel,
                        user_weights,
                        valid,
                        record_history=False,
                        include_wealth=compare_optimizer,
                        sampled_risk_free=valid_rf,
                        synthetic=True,
                    )
                    user_metrics = simulation_metrics(
                        control_sample["controlled"], include_wealth=compare_optimizer
                    )
                    control_sample["controlled"].pop("net_wealth", None)
                else:
                    user_metrics = fixed_portfolio_metrics(
                        values,
                        fixed_weights,
                        valid_rf,
                        include_wealth=compare_optimizer,
                        **panel.path_options(valid, synthetic=True),
                    )
            except Exception as exc:  # A failed control path must not drop the generation.
                control_sample = {"status": "failed", "reason": str(exc)}
            if controls_enabled:
                control_samples.append({**control_sample, "indices_sha256": validation_hash})

        for objective in fitted:
            records[objective].append(
                _validation_record(
                    fold_fits[objective],
                    offsets,
                    values,
                    valid_rf,
                    panel,
                    valid,
                    iteration,
                    compare_optimizer and objective == comparison_objective,
                )
            )

        if fixed_weights is not None:
            distance = {"status": "disabled", "diagnostics": {}}
            if include_efficiency_robustness:
                try:
                    distance = efficiency_distance(
                        values, np.cov(values, rowvar=False), fixed_weights, constraints
                    )
                except Exception as exc:  # Optional ex-post diagnostics cannot change the fit.
                    distance = {"status": "failed", "diagnostics": {"reason": str(exc)}}
            efficiency_records.append(
                {
                    "iteration": iteration,
                    **distance,
                    "observations": observations,
                    "portfolio_metrics": {
                        k: v for k, v in (user_metrics or {}).items() if k != "net_wealth"
                    },
                }
            )
        if compare_optimizer:
            optimized = records[comparison_objective][-1]
            comparison, cloud, mean, covariance = joined_validation_comparison(
                iteration,
                values,
                panel.assets,
                user_metrics,
                optimized,
                validation_hash,
                user_failure=(control_sample or {}).get("reason"),
            )
            comparisons.append(comparison)
            if cloud is not None:
                clouds.append(cloud)
            validation_mean += mean
            validation_covariance += covariance

    results = {}
    for objective in objectives:
        rows = records[objective]
        summary = _bootstrap_objective_summary(objective, rows, constraints, target_return)
        summary.update(
            summarize_bootstrap_portfolio(
                [{"portfolio_metrics": row["metrics"] or {}} for row in rows], observations
            )
        )
        results[objective] = summary
    paired = None
    if compare_optimizer:
        paired = {
            **summarize(comparisons, metrics=JOINED_METRICS),
            "analysis_version": BOOTSTRAP_METHODOLOGY,
            "generated_datasets": bootstrap_iterations,
            "horizon_months": observations,
            "user_portfolio_mode": "controlled" if controls_enabled else "passive",
            "user_weights": dict(zip(panel.assets, fixed_weights.tolist(), strict=True)),
            "clouds": clouds,
            **validation_frontier(
                validation_mean / bootstrap_iterations,
                validation_covariance / bootstrap_iterations,
                constraints,
            ),
        }
    return {
        **data_diagnostics(panel, descriptive, risk_free_rate, include_asset_portfolios=False),
        "status": "complete"
        if all(r["status"] == "complete" for r in results.values())
        else "incomplete",
        "assets": panel.assets,
        "currency": "USD",
        "risk_free_rate": risk_free_rate,
        "methodology": BOOTSTRAP_METHODOLOGY,
        "expected_block_length": expected_block_length,
        "restart_probability": 1 / expected_block_length,
        "random_seed": random_seed,
        "sample_length": observations,
        "sample_fingerprint": fingerprint.hexdigest(),
        "validation_sample_fingerprint": validation_fingerprint.hexdigest(),
        "cv_folds": cv_folds,
        "scenario_counts": {
            "generations": bootstrap_iterations,
            "synthetic_train": bootstrap_iterations * cv_folds,
            "synthetic_validation": bootstrap_iterations * cv_folds,
            "concatenated_validation": bootstrap_iterations,
        },
        "outer_folds": outer_folds,
        "generations": generations,
        "index_replay": "pcg64_seed_sequence_seed_generation_fold_side_zero_based_v1",
        "conditional_bootstrap": False,
        "hyperparameter_uncertainty_included": True,
        "covariance_method": method,
        "covariance_parameters": {"scope": "synthetic_train_per_fold"},
        "covariance_diagnostics": {"scope": "synthetic_train_per_fold"},
        "source_metadata": panel.metadata,
        "control_source_metadata": panel.control_metadata,
        "portfolio_evaluation": "continuous_concatenated_validation",
        "objectives": results,
        "portfolio_control_samples": control_samples,
        "passive_portfolio_metrics": summarize_bootstrap_portfolio(
            [
                {"portfolio_metrics": simulation_metrics(s["baseline"]) if "baseline" in s else {}}
                for s in control_samples
            ],
            observations,
        )
        if controls_enabled
        else None,
        "paired_comparison": paired,
        "efficiency_robustness": {
            "user_portfolio_mode": "controlled" if controls_enabled else "passive",
            "user_weights": dict(zip(panel.assets, fixed_weights.tolist(), strict=True)),
            "evaluation": "concatenated_validation_scenarios",
            "distance_reference": "ex_post_fixed_user_weights",
            **summarize_distances(efficiency_records),
            **summarize_bootstrap_portfolio(efficiency_records, observations),
        }
        if fixed_weights is not None
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
        and cache.get("tax_methodology") == TAX_METHODOLOGY
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
                                panel, user_weights, indices, record_history=False, synthetic=True
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
                "trading_costs": {k: path[k] for k in TRADING_METRICS},
                "tax_insolvent": path["tax_insolvent"],
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
