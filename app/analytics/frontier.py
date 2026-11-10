import time

import numpy as np

from app.analytics.constraints import build_constraints, user_allocation
from app.analytics.estimators import estimate_covariance
from app.analytics.metrics import correlation_diagnostics, portfolio_metrics
from app.analytics.models import AnalyticsError, OptimizationResult, ReturnPanel
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.provenance import panel_provenance, reproducible
from app.analytics.references import differential_moments


def prepare(
    panel: ReturnPanel,
    *,
    covariance_method: str = "sample",
    cv_folds: int = 4,
    shrinkage_target: str = "identity",
    asset_constraints: dict | None = None,
    group_constraints: dict | None = None,
) -> tuple:
    if panel.risk_free_source == "historical_bil" and panel.risk_free_returns is None:
        raise AnalyticsError("Historical BIL mode requires aligned monthly BIL observations.")
    constraints = build_constraints(
        panel.assets, asset_constraints, group_constraints, panel.groups
    )
    estimate = estimate_covariance(
        panel.values, covariance_method, folds=cv_folds, target=shrinkage_target
    )
    panel.differential = differential_moments(
        panel.values,
        panel.risk_free_returns,
        covariance_method,
        folds=cv_folds,
        target=shrinkage_target,
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
            result.weights,
            panel.values,
            mean,
            covariance,
            risk_free_rate,
            risk_free_returns=panel.risk_free_returns,
            differential=panel.differential,
        )
    return output


def data_diagnostics(panel: ReturnPanel, estimate, risk_free_rate: float = 0.03) -> dict:
    correlation = correlation_diagnostics(panel.values, panel.assets)
    warnings = panel.warnings + correlation["warnings"]
    if estimate.diagnostics["nearly_singular"]:
        warnings.append("Nearly singular covariance: a disclosed eigenvalue floor was applied.")
    return {
        "return_basis": panel.return_basis,
        "risk_free_source": panel.risk_free_source,
        "reference_metadata": panel.reference_metadata,
        "risk_free_mean_return": float(panel.risk_free_returns.mean() * 12)
        if panel.risk_free_returns is not None
        else risk_free_rate,
        "differential_covariance_parameters": panel.differential["differential_estimate"].parameters
        if panel.differential
        else {},
        "differential_covariance_diagnostics": panel.differential[
            "differential_estimate"
        ].diagnostics
        if panel.differential
        else {},
        "annual_differential_covariance": panel.differential["differential_covariance"].tolist()
        if panel.differential
        else None,
        "annual_differential_mean": panel.differential["differential_mean"].tolist()
        if panel.differential
        else None,
        "asset_portfolios": {
            asset: describe(
                OptimizationResult("reference", weights, {"asset_id": asset}),
                panel,
                panel.values.mean(0) * 12,
                estimate.matrix * 12,
                risk_free_rate,
            )
            for asset, weights in zip(panel.assets, np.eye(len(panel.assets)), strict=True)
        },
        "reproducibility": panel_provenance(panel),
        "allocation_groups": panel.groups,
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


@reproducible
def analyze_frontier(
    panel: ReturnPanel,
    *,
    frontier_points: int = 51,
    risk_free_rate: float = 0.03,
    objective: str = "gmv",
    target_return: float | None = None,
    user_weights: dict | None = None,
    **options,
) -> dict:
    if not 2 <= frontier_points <= 201 or not np.isfinite(risk_free_rate):
        raise AnalyticsError(
            "Frontier points must be 2 to 201 and the risk-free rate must be finite."
        )
    started = time.perf_counter()
    allocation = user_allocation(panel.assets, user_weights)
    constraints, estimate, mean, covariance = prepare(panel, **options)
    optimizer = PortfolioOptimizer(mean, covariance, constraints, **panel.differential)
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
    user = None
    if allocation is not None:
        user = describe(
            OptimizationResult("reference", allocation, {}),
            panel,
            mean,
            covariance,
            risk_free_rate,
        )
        user["feasible"] = constraints.violation(allocation) <= 1e-7
        user["diagnostics"]["constraint_violation"] = constraints.violation(allocation)
        user["label"] = "User portfolio" if user["feasible"] else "Unconstrained user reference"
    selected = optimizer.optimize(objective, risk_free_rate, target_return)
    success = all(r.weights is not None for r in (gmv, maximum, selected)) and all(
        p["status"] == "optimal" for p in points
    )
    return {
        "status": "complete" if success and sharpe.weights is not None else "incomplete",
        "assets": panel.assets,
        "currency": "USD",
        **data_diagnostics(panel, estimate, risk_free_rate),
        "source_metadata": panel.metadata,
        "annual_expected_returns": dict(zip(panel.assets, mean.tolist(), strict=True)),
        "annual_covariance": covariance.tolist(),
        "covariance_method": estimate.method,
        "covariance_parameters": estimate.parameters,
        "covariance_diagnostics": estimate.diagnostics,
        "risk_free_rate": risk_free_rate,
        "gmv": describe(gmv, panel, mean, covariance, risk_free_rate),
        "max_sharpe": {
            **describe(sharpe, panel, mean, covariance, risk_free_rate),
            "label": "Max Sharpe · BIL differential"
            if panel.risk_free_source == "historical_bil"
            else "Max Sharpe",
        },
        "maximum_return": describe(maximum, panel, mean, covariance, risk_free_rate),
        "selected_portfolio": describe(selected, panel, mean, covariance, risk_free_rate),
        "equal_weight": equal,
        "user_portfolio": user,
        "frontier": points,
        "execution_seconds": time.perf_counter() - started,
    }
