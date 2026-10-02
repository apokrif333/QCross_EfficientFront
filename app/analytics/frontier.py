import time

import numpy as np

from app.analytics.constraints import build_constraints
from app.analytics.estimators import estimate_covariance
from app.analytics.metrics import correlation_diagnostics, portfolio_metrics
from app.analytics.models import AnalyticsError, OptimizationResult, ReturnPanel
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.provenance import panel_provenance


def prepare(
    panel: ReturnPanel,
    *,
    covariance_method: str = "sample",
    cv_folds: int = 5,
    shrinkage_target: str = "identity",
    asset_constraints: dict | None = None,
    group_constraints: dict | None = None,
) -> tuple:
    constraints = build_constraints(
        panel.assets, asset_constraints, group_constraints, panel.groups
    )
    estimate = estimate_covariance(
        panel.values, covariance_method, folds=cv_folds, target=shrinkage_target
    )
    mean = panel.values.mean(0) * 12
    covariance = estimate.matrix * 12
    return constraints, estimate, mean, covariance


def describe(
    result: OptimizationResult,
    panel: ReturnPanel,
    mean: np.ndarray,
    covariance: np.ndarray,
    risk_free_rate: float,
) -> dict:
    output = {
        "status": result.status,
        "diagnostics": result.diagnostics,
        "weights": None,
        "metrics": None,
    }
    if result.weights is not None:
        output["weights"] = dict(zip(panel.assets, result.weights.tolist(), strict=True))
        output["metrics"] = portfolio_metrics(
            result.weights, panel.values, mean, covariance, risk_free_rate
        )
    return output


def data_diagnostics(panel: ReturnPanel, estimate) -> dict:
    correlation = correlation_diagnostics(panel.values, panel.assets)
    warnings = panel.warnings + correlation["warnings"]
    if estimate.diagnostics["nearly_singular"]:
        warnings.append("Nearly singular covariance: a disclosed eigenvalue floor was applied.")
    return {
        "reproducibility": panel_provenance(panel),
        "period": {
            "start": str(panel.returns.index[0]),
            "end": str(panel.returns.index[-1]),
            "observations": len(panel.values),
        },
        "annual_expected_returns": dict(
            zip(panel.assets, (panel.values.mean(0) * 12).tolist(), strict=True)
        ),
        "annual_covariance": (estimate.matrix * 12).tolist(),
        "correlation": correlation["matrix"],
        "warnings": list(dict.fromkeys(warnings)),
    }


def analyze_frontier(
    panel: ReturnPanel,
    *,
    frontier_points: int = 51,
    risk_free_rate: float = 0.03,
    objective: str = "gmv",
    target_return: float | None = None,
    **options,
) -> dict:
    if not 2 <= frontier_points <= 201 or not np.isfinite(risk_free_rate):
        raise AnalyticsError(
            "Frontier points must be 2 to 201 and the risk-free rate must be finite."
        )
    started = time.perf_counter()
    constraints, estimate, mean, covariance = prepare(panel, **options)
    optimizer = PortfolioOptimizer(mean, covariance, constraints)
    gmv, maximum, sharpe = (
        optimizer.gmv(),
        optimizer.maximum_return(),
        optimizer.max_sharpe(risk_free_rate),
    )
    points = []
    if gmv.weights is not None and maximum.weights is not None:
        for target in np.linspace(mean @ gmv.weights, mean @ maximum.weights, frontier_points):
            point = describe(
                optimizer.target_return(float(target)), panel, mean, covariance, risk_free_rate
            )
            point["target_return"] = float(target)
            points.append(point)
    equal_weights = np.full(len(panel.assets), 1 / len(panel.assets))
    equal = describe(
        OptimizationResult("reference", equal_weights, {}), panel, mean, covariance, risk_free_rate
    )
    equal["feasible"] = constraints.violation(equal_weights) <= 1e-7
    equal["label"] = (
        "Equal-weight benchmark" if equal["feasible"] else "Unconstrained equal-weight reference"
    )
    selected = optimizer.optimize(objective, risk_free_rate, target_return)
    success = all(r.weights is not None for r in (gmv, maximum, selected)) and all(
        p["status"] == "optimal" for p in points
    )
    return {
        "status": "complete" if success and sharpe.weights is not None else "incomplete",
        "assets": panel.assets,
        "currency": "USD",
        **data_diagnostics(panel, estimate),
        "source_metadata": panel.metadata,
        "annual_expected_returns": dict(zip(panel.assets, mean.tolist(), strict=True)),
        "annual_covariance": covariance.tolist(),
        "covariance_method": estimate.method,
        "covariance_parameters": estimate.parameters,
        "covariance_diagnostics": estimate.diagnostics,
        "risk_free_rate": risk_free_rate,
        "gmv": describe(gmv, panel, mean, covariance, risk_free_rate),
        "max_sharpe": describe(sharpe, panel, mean, covariance, risk_free_rate),
        "maximum_return": describe(maximum, panel, mean, covariance, risk_free_rate),
        "selected_portfolio": describe(selected, panel, mean, covariance, risk_free_rate),
        "equal_weight": equal,
        "frontier": points,
        "execution_seconds": time.perf_counter() - started,
    }
