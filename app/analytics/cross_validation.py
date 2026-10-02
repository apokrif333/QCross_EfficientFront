"""Classical unshuffled K-fold portfolio validation, not walk-forward validation."""

import time

import numpy as np
from sklearn.model_selection import KFold

from app.analytics.estimators import estimate_covariance
from app.analytics.frontier import data_diagnostics, describe, prepare
from app.analytics.metrics import historical_metrics, portfolio_metrics
from app.analytics.models import AnalyticsError, OptimizationResult, ReturnPanel
from app.analytics.optimizer import PortfolioOptimizer


def portfolio_folds(observations: int, folds: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    if not 2 <= folds <= 10:
        raise AnalyticsError("Classical K-fold CV requires K from 2 to 10.")
    training = observations - int(np.ceil(observations / folds))
    if training < 120:
        raise AnalyticsError(
            f"{folds}-fold CV leaves only {training} training months per largest validation fold. "
            "At least 120 training months are required (T - ceil(T/K) >= 120)."
        )
    return list(KFold(n_splits=folds, shuffle=False).split(np.arange(observations)))


def period_description(panel: ReturnPanel, indices: np.ndarray) -> dict:
    # Training is usually disjoint; explicitly disclose every consecutive segment.
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


def analyze_cross_validation(
    panel: ReturnPanel,
    *,
    cv_folds: int = 5,
    objective: str = "gmv",
    target_return: float | None = None,
    risk_free_rate: float = 0.03,
    **options,
) -> dict:
    started = time.perf_counter()
    splits = portfolio_folds(len(panel.values), cv_folds)
    constraints, estimate, mean, covariance = prepare(panel, cv_folds=cv_folds, **options)
    original = PortfolioOptimizer(mean, covariance, constraints).optimize(
        objective, risk_free_rate, target_return
    )
    fold_results, fold_weights = [], []
    for number, (training, validation) in enumerate(splits, 1):
        train_values, validation_values = panel.values[training], panel.values[validation]
        fold = {
            "fold": number,
            "training": period_description(panel, training),
            "validation": period_description(panel, validation),
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
            result = PortfolioOptimizer(training_mean, training_covariance, constraints).optimize(
                objective, risk_free_rate, target_return
            )
            fold.update(
                status=result.status,
                diagnostics=result.diagnostics,
                weights=None,
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
                    weights, train_values, training_mean, training_covariance, risk_free_rate
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
                }
        except (AnalyticsError, np.linalg.LinAlgError) as exc:
            fold.update(status="failed", weights=None, diagnostics={"reason": str(exc)})
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
        **data_diagnostics(panel, estimate),
        "status": "complete" if ensemble else "incomplete",
        "assets": panel.assets,
        "currency": "USD",
        "objective": objective,
        "target_return": target_return,
        "risk_free_rate": risk_free_rate,
        "cv_folds": cv_folds,
        "methodology": "classical_consecutive_kfold_no_shuffle",
        "ensemble_evaluation": "full_sample_descriptive_not_independent_out_of_sample",
        "covariance_method": estimate.method,
        "covariance_parameters": estimate.parameters,
        "covariance_diagnostics": estimate.diagnostics,
        "period": period_description(panel, np.arange(len(panel.values))),
        "source_metadata": panel.metadata,
        "original": describe(original, panel, mean, covariance, risk_free_rate),
        "folds": fold_results,
        "ensemble": ensemble,
        "stability": stability,
        "execution_seconds": time.perf_counter() - started,
    }
