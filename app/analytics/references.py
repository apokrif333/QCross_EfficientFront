"""Vectorized basis conversion and differential moments; no database access."""

from dataclasses import replace

import numpy as np

from app.analytics.estimators import estimate_covariance
from app.analytics.models import AnalyticsError


def real_returns(values, inflation):
    inflation = np.asarray(inflation, dtype=float)
    if not np.isfinite(inflation).all() or np.any(inflation <= -1):
        raise AnalyticsError("Inflation must be finite and greater than -100%.")
    values = np.asarray(values, dtype=float)
    if values.ndim not in (1, 2) or len(values) != len(inflation):
        raise AnalyticsError("Inflation and returns must use exactly the same monthly rows.")
    divisor = 1 + inflation[:, None] if values.ndim == 2 else 1 + inflation
    result = (1 + values) / divisor - 1
    if not np.isfinite(result).all():
        raise AnalyticsError("Real returns are not numerically finite.")
    return result


def differential_moments(
    values, risk_free, method="sample", *, folds=4, target="identity", fixed_parameters=None
):
    if risk_free is None:
        return {}
    differential = values - np.asarray(risk_free)[:, None]
    active = np.any(differential != 0, axis=0)
    if not active.all():
        if not active.any():
            from app.analytics.models import CovarianceEstimate

            estimate = CovarianceEstimate(
                np.zeros((values.shape[1], values.shape[1])),
                method,
                {"deterministic_zero_differential": True},
                {},
            )
        else:
            sub = estimate_covariance(
                differential[:, active],
                method,
                folds=folds,
                target=target,
                fixed_parameters=fixed_parameters,
            )
            matrix = np.zeros((values.shape[1], values.shape[1]))
            matrix[np.ix_(active, active)] = sub.matrix
            estimate = replace(
                sub,
                matrix=matrix,
                diagnostics={
                    **sub.diagnostics,
                    "zero_differential_columns": np.flatnonzero(~active).tolist(),
                },
            )
        return {
            "differential_mean": differential.mean(0) * 12,
            "differential_covariance": estimate.matrix * 12,
            "differential_estimate": estimate,
        }
    estimate = estimate_covariance(
        differential, method, folds=folds, target=target, fixed_parameters=fixed_parameters
    )
    return {
        "differential_mean": differential.mean(0) * 12,
        "differential_covariance": estimate.matrix * 12,
        "differential_estimate": estimate,
    }


def sharpe_metrics(weights, values, risk_free, mean, covariance):
    differences = values @ weights - risk_free
    deviation = float(differences.std(ddof=1))
    modeled = float(np.sqrt(max(0, weights @ covariance @ weights)))
    return {
        "sharpe_ratio": float(weights @ mean / modeled)
        if modeled > 1e-10 and deviation > 1e-12
        else None,
        "historical_sharpe_ratio": float(np.sqrt(12) * differences.mean() / deviation)
        if deviation > 1e-12
        else None,
        "risk_free_mean_return": float(np.mean(risk_free) * 12),
    }
