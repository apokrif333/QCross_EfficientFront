"""Bidirectional one-sided block portfolio CV for allocation sample sensitivity."""

import time

import numpy as np

from app.analytics.constraints import user_allocation
from app.analytics.efficiency_distance import efficiency_distance, summarize_distances
from app.analytics.estimators import estimate_covariance
from app.analytics.frontier import data_diagnostics, describe, prepare
from app.analytics.metrics import historical_metrics, portfolio_metrics
from app.analytics.models import AnalyticsError, OptimizationResult, ReturnPanel
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.paired_comparison import compare_validation, summarize
from app.analytics.provenance import reproducible
from app.analytics.references import differential_moments
from app.analytics.splitting import (
    CV_METHODOLOGY,
    MIN_COVARIANCE_TRAIN,
    MIN_COVARIANCE_VALIDATION,
    MIN_PORTFOLIO_TRAIN,
    MIN_PORTFOLIO_VALIDATION,
    eligible_folds,
    fold_direction,
    one_sided_folds,
    validate_cv_configuration,
    validation_metadata,
)


def portfolio_folds(observations: int, folds: int = 4) -> list[tuple[np.ndarray, np.ndarray]]:
    return one_sided_folds(
        observations, folds, min_train=MIN_PORTFOLIO_TRAIN, min_validation=MIN_PORTFOLIO_VALIDATION
    )


def period_description(panel: ReturnPanel, indices: np.ndarray) -> dict:
    # Preserve the segments field; one-sided folds always have exactly one segment.
    segments = np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1)
    return {
        "start": str(panel.returns.index[indices[0]]),
        "end": str(panel.returns.index[indices[-1]]),
        "observations": len(indices),
        "segments": [
            {
                "start": str(panel.returns.index[s[0]]),
                "end": str(panel.returns.index[s[-1]]),
                "observations": len(s),
            }
            for s in segments
        ],
    }


def weight_stability(weights: np.ndarray, original: np.ndarray, constraints) -> dict:
    center = weights.mean(0)
    deviations = np.abs(weights - center)
    return {
        "standard_deviation": dict(
            zip(constraints.assets, weights.std(0, ddof=0).tolist(), strict=True)
        ),
        "minimum": dict(zip(constraints.assets, weights.min(0).tolist(), strict=True)),
        "maximum": dict(zip(constraints.assets, weights.max(0).tolist(), strict=True)),
        "mean_absolute_weight_deviation": float(deviations.mean()),
        "maximum_weight_deviation": float(deviations.max()),
        "mean_allocation_turnover_from_original": float(
            np.abs(weights - original).sum(1).mean() / 2
        ),
        "lower_bound_frequency": dict(
            zip(
                constraints.assets,
                np.mean(np.abs(weights - constraints.lower) <= 1e-6, axis=0).tolist(),
                strict=True,
            )
        ),
        "upper_bound_frequency": dict(
            zip(
                constraints.assets,
                np.mean(np.abs(weights - constraints.upper) <= 1e-6, axis=0).tolist(),
                strict=True,
            )
        ),
    }


@reproducible
def analyze_cross_validation(
    panel: ReturnPanel,
    *,
    cv_folds: int = 4,
    objective: str = "gmv",
    target_return: float | None = None,
    risk_free_rate: float = 0.03,
    user_weights: dict | None = None,
    include_efficiency_robustness: bool = True,
    include_paired_comparison: bool = True,
    **options,
) -> dict:
    started = time.perf_counter()
    splits = validate_cv_configuration(
        len(panel.values),
        cv_folds,
        portfolio=True,
        covariance_method=options.get("covariance_method", "sample"),
    )
    constraints, estimate, mean, covariance = prepare(panel, cv_folds=cv_folds, **options)
    fixed_user_weights = user_allocation(panel.assets, user_weights) if user_weights else None
    original = PortfolioOptimizer(mean, covariance, constraints, **panel.differential).optimize(
        objective, risk_free_rate, target_return, fixed_user_weights
    )
    fold_results, fold_weights = [], []
    for number, (training, validation) in enumerate(splits, 1):
        training_mean, training_covariance = None, None
        train_values, validation_values = panel.values[training], panel.values[validation]
        fold = {
            "fold": number,
            "training": period_description(panel, training),
            "validation": period_description(panel, validation),
            "training_direction": fold_direction(training, validation),
            "training_metrics": None,
            "validation_metrics": None,
            "covariance_parameters": None,
            "covariance_diagnostics": None,
        }
        try:
            # Inner covariance selection sees ONLY the outer training array.
            training_estimate = estimate_covariance(
                train_values,
                options.get("covariance_method", "sample"),
                folds=cv_folds,
                target=options.get("shrinkage_target", "identity"),
            )
            training_mean, training_covariance = (
                train_values.mean(0) * 12,
                training_estimate.matrix * 12,
            )
            training_rf = (
                panel.risk_free_returns[training] if panel.risk_free_returns is not None else None
            )
            differential = differential_moments(
                train_values,
                training_rf,
                options.get("covariance_method", "sample"),
                folds=cv_folds,
                target=options.get("shrinkage_target", "identity"),
            )
            result = PortfolioOptimizer(
                training_mean, training_covariance, constraints, **differential
            ).optimize(objective, risk_free_rate, target_return, fixed_user_weights)
            fold.update(
                status=result.status,
                diagnostics=result.diagnostics,
                weights=None,
                differential_covariance_parameters=differential["differential_estimate"].parameters
                if differential
                else {},
                differential_covariance_diagnostics=differential[
                    "differential_estimate"
                ].diagnostics
                if differential
                else {},
                covariance_parameters=training_estimate.parameters,
                covariance_diagnostics=training_estimate.diagnostics,
                training_metrics=None,
                validation_metrics=None,
            )
            if result.weights is not None:
                weights = result.weights
                fold_weights.append(weights)
                fold["weights"] = dict(zip(panel.assets, weights.tolist(), strict=True))
                fold["training_metrics"] = portfolio_metrics(
                    weights,
                    train_values,
                    training_mean,
                    training_covariance,
                    risk_free_rate,
                    risk_free_returns=training_rf,
                    differential=differential,
                )
                realized = validation_values @ weights
                realized_return = float(realized.mean() * 12)
                realized_volatility = float(realized.std(ddof=1) * np.sqrt(12))
                fold["validation_metrics"] = {
                    "realized_annual_arithmetic_return": realized_return,
                    "realized_annual_volatility": realized_volatility,
                    "realized_sharpe_ratio": (realized_return - risk_free_rate)
                    / realized_volatility
                    if len(realized) >= 2 and realized_volatility > 1e-10
                    else None,
                    "realized_total_return": float(np.prod(1 + realized) - 1),
                    **historical_metrics(realized),
                    "historical_sharpe_ratio": None,
                }
                validation_rf = (
                    panel.risk_free_returns[validation]
                    if panel.risk_free_returns is not None
                    else np.full(len(validation), (1 + risk_free_rate) ** (1 / 12) - 1)
                )
                differences = realized - validation_rf
                deviation = differences.std(ddof=1)
                ratio = (
                    float(np.sqrt(12) * differences.mean() / deviation)
                    if deviation > 1e-12
                    else None
                )
                fold["validation_metrics"].update(
                    {
                        "historical_sharpe_ratio": ratio,
                        "risk_free_mean_return": float(validation_rf.mean() * 12),
                        "realized_sharpe_ratio": ratio,
                    }
                )
        except (AnalyticsError, np.linalg.LinAlgError) as exc:
            fold.update(status="failed", weights=None, diagnostics={"reason": str(exc)})
        if fixed_user_weights is not None:
            try:
                method = options.get("covariance_method", "sample")
                fixed_parameters = None
                if method == "cv_linear":
                    fixed_parameters = fold.get("covariance_parameters")
                    if not fixed_parameters:
                        raise AnalyticsError(
                            "Training fold did not produce cv_linear shrinkage parameters."
                        )
                validation_estimate = estimate_covariance(
                    validation_values,
                    method,
                    folds=cv_folds,
                    target=options.get("shrinkage_target", "identity"),
                    fixed_parameters=fixed_parameters,
                )
                fold["efficiency_robustness"] = efficiency_distance(
                    validation_values,
                    validation_estimate.matrix,
                    fixed_user_weights,
                    constraints,
                    estimator_parameters=validation_estimate.parameters,
                    covariance_diagnostics=validation_estimate.diagnostics,
                )
            except Exception as exc:  # Optional diagnostics do not change CV fold outcomes.
                fold["efficiency_robustness"] = {
                    "status": "failed",
                    "diagnostics": {"reason": str(exc)},
                }
        if fixed_user_weights is not None and include_paired_comparison:
            try:
                if training_mean is None or training_covariance is None:
                    raise AnalyticsError("Training covariance unavailable for paired comparison.")
                trained = PortfolioOptimizer(
                    training_mean, training_covariance, constraints
                ).max_return_at_user_risk(fixed_user_weights)
                trained.diagnostics.update(
                    covariance_parameters=training_estimate.parameters,
                    covariance_diagnostics=training_estimate.diagnostics,
                )
                rf = (
                    panel.risk_free_returns[validation]
                    if panel.risk_free_returns is not None
                    else np.full(len(validation), (1 + risk_free_rate) ** (1 / 12) - 1)
                )
                fold["paired_comparison"] = compare_validation(
                    validation_values, rf, fixed_user_weights, trained, panel.assets
                )
                fold["paired_comparison"].update(
                    fold=number,
                    training_direction=fold["training_direction"],
                    validation=fold["validation"],
                )
            except Exception as exc:
                fold["paired_comparison"] = {
                    "status": "failed",
                    "diagnostics": {"reason": str(exc)},
                }
        fold_results.append(fold)
    ensemble, stability = None, None
    if len(fold_weights) == cv_folds and original.weights is not None:
        weights = np.stack(fold_weights)
        average = weights.mean(0)
        constraints.require_feasible(average)
        ensemble = describe(
            OptimizationResult(
                "averaged", average, {"constraint_violation": constraints.violation(average)}
            ),
            panel,
            mean,
            covariance,
            risk_free_rate,
        )
        stability = weight_stability(weights, original.weights, constraints)
    return {
        **data_diagnostics(panel, estimate, risk_free_rate),
        "status": "complete" if ensemble else "incomplete",
        "assets": panel.assets,
        "currency": "USD",
        "objective": objective,
        "target_return": target_return,
        "risk_free_rate": risk_free_rate,
        "cv_folds": cv_folds,
        "methodology": CV_METHODOLOGY,
        **validation_metadata(len(panel.values), splits),
        "minimum_portfolio_train": MIN_PORTFOLIO_TRAIN,
        "minimum_portfolio_validation": MIN_PORTFOLIO_VALIDATION,
        "minimum_covariance_train": MIN_COVARIANCE_TRAIN,
        "minimum_covariance_validation": MIN_COVARIANCE_VALIDATION,
        "eligible_portfolio_folds": eligible_folds(
            len(panel.values),
            min_train=MIN_PORTFOLIO_TRAIN,
            min_validation=MIN_PORTFOLIO_VALIDATION,
        ),
        "validation_interpretation": (
            "Sample sensitivity and allocation stability diagnostics. Some folds train on "
            "later observations; validation is not a historical investable backtest or "
            "independent forward out-of-sample performance. No embargo or purge gap."
        ),
        "ensemble_evaluation": "full_sample_descriptive_not_independent_out_of_sample",
        "covariance_method": estimate.method,
        "covariance_parameters": estimate.parameters,
        "covariance_diagnostics": estimate.diagnostics,
        "period": period_description(panel, np.arange(len(panel.values))),
        "source_metadata": panel.metadata,
        "original": describe(original, panel, mean, covariance, risk_free_rate),
        "folds": fold_results,
        "paired_comparison": summarize(
            [f["paired_comparison"] for f in fold_results if "paired_comparison" in f]
        )
        if fixed_user_weights is not None and include_paired_comparison
        else None,
        "efficiency_robustness": {
            "user_weights": dict(zip(panel.assets, fixed_user_weights.tolist(), strict=True)),
            "evaluation": "retrospective_validation_sample_frontiers",
            **summarize_distances(
                [f["efficiency_robustness"] for f in fold_results if f.get("efficiency_robustness")]
            ),
        }
        if fixed_user_weights is not None
        else None,
        "ensemble": ensemble,
        "stability": stability,
        "execution_seconds": time.perf_counter() - started,
    }
