"""Exact constrained distances from a fixed allocation to a sample frontier."""

import time

import cvxpy as cp
import numpy as np

from app.analytics.constraints import AllocationConstraints

SOLVER_TOLERANCE = 1e-7


def _solve(problem: cp.Problem) -> tuple[str, str | None]:
    try:
        problem.solve(solver="CLARABEL", warm_start=True, max_iter=200)
    except cp.SolverError as exc:
        return "solver_failed", str(exc)
    if problem.status not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
        return "solver_failed", str(problem.status)
    return str(problem.status), None


def efficiency_distance(
    values: np.ndarray,
    covariance: np.ndarray,
    user_weights: np.ndarray,
    constraints: AllocationConstraints,
    *,
    estimator_parameters: dict | None = None,
    covariance_diagnostics: dict | None = None,
) -> dict:
    """Calculate annual return shortfall and excess risk with exact convex solves.

    ``values`` and ``covariance`` are monthly. User weights are never changed.
    Negative distances within solver tolerance are reported as zero; larger negative
    values are surfaced as numerical inconsistencies instead of being concealed.
    """
    started = time.perf_counter()
    values, covariance = np.asarray(values, dtype=float), np.asarray(covariance, dtype=float)
    weights = np.asarray(user_weights, dtype=float)
    weight_labels = (
        dict(zip(constraints.assets, weights.tolist(), strict=True))
        if weights.ndim == 1 and weights.size == len(constraints.assets)
        else {}
    )
    result = {
        "status": "not_comparable",
        "user_weights": weight_labels,
        "return_shortfall": None,
        "excess_volatility": None,
        "diagnostics": {
            "estimator_parameters": estimator_parameters or {},
            "covariance_diagnostics": covariance_diagnostics or {},
            "solver": "CLARABEL",
            "solver_tolerance": SOLVER_TOLERANCE,
        },
    }
    if (
        values.ndim != 2
        or values.shape[1] != len(constraints.assets)
        or weights.shape != (len(constraints.assets),)
        or not np.isfinite(weights).all()
    ):
        result["diagnostics"]["reason"] = "Shape mismatch between returns, weights and assets."
        return result
    if (
        len(values) < 2
        or covariance.shape != (weights.size, weights.size)
        or not np.isfinite(values).all()
        or not np.isfinite(covariance).all()
    ):
        result["diagnostics"]["reason"] = "Insufficient observations or invalid covariance shape."
        return result
    mean = values.mean(axis=0) * 12
    annual_covariance = (covariance + covariance.T) * 6
    scale = max(float(np.max(np.abs(annual_covariance))), 1e-14)
    if np.linalg.eigvalsh(annual_covariance)[0] < -scale * 1e-8:
        result.update(status="numerical_failure")
        result["diagnostics"]["reason"] = "Covariance has materially negative eigenvalues."
        return result
    mu_user = float(mean @ weights)
    variance_user = float(weights @ annual_covariance @ weights)
    scale = max(float(np.max(np.abs(annual_covariance))), 1e-12)
    if variance_user < -scale * SOLVER_TOLERANCE:
        result.update(status="numerical_failure")
        result["diagnostics"]["reason"] = "User portfolio variance is materially negative."
        return result
    sigma_user = float(np.sqrt(max(variance_user, 0)))
    result.update(user_expected_return=mu_user, user_volatility=sigma_user)
    violation = constraints.violation(weights)
    result["diagnostics"]["constraint_violation"] = violation
    if not np.isfinite(weights).all() or violation > SOLVER_TOLERANCE:
        result["diagnostics"]["reason"] = "User allocation violates selected constraints."
        return result

    w_return = cp.Variable(weights.size)
    same_risk = cp.Problem(
        cp.Maximize(mean @ w_return),
        constraints.cvx(w_return) + [cp.quad_form(w_return, annual_covariance) <= variance_user],
    )
    status_return, error_return = _solve(same_risk)
    if (
        error_return
        or w_return.value is None
        or not np.isfinite(w_return.value).all()
        or same_risk.value is None
        or not np.isfinite(same_risk.value)
    ):
        result.update(
            status="solver_failed",
            diagnostics={
                **result["diagnostics"],
                "return_solver": status_return,
                "return_error": error_return,
            },
        )
        return result
    delta_mu = float(same_risk.value - mu_user)

    w_risk = cp.Variable(weights.size)
    same_return = cp.Problem(
        cp.Minimize(cp.quad_form(w_risk, annual_covariance)),
        constraints.cvx(w_risk) + [mean @ w_risk >= mu_user],
    )
    status_risk, error_risk = _solve(same_return)
    if (
        error_risk
        or w_risk.value is None
        or not np.isfinite(w_risk.value).all()
        or same_return.value is None
        or not np.isfinite(same_return.value)
    ):
        result.update(
            status="solver_failed",
            diagnostics={
                **result["diagnostics"],
                "risk_solver": status_risk,
                "risk_error": error_risk,
            },
        )
        return result
    sigma_min = float(np.sqrt(max(float(same_return.value), 0)))
    delta_sigma = sigma_user - sigma_min
    tolerance = SOLVER_TOLERANCE * max(1.0, abs(mu_user), sigma_user)
    return_violation = max(
        0.0,
        float(
            np.sqrt(max(float(w_return.value @ annual_covariance @ w_return.value), 0)) - sigma_user
        ),
    )
    return_shortfall = max(0.0, mu_user - float(mean @ w_risk.value))
    allocation_violation = max(
        constraints.violation(w_return.value), constraints.violation(w_risk.value)
    )
    result["diagnostics"].update(
        return_solver=status_return,
        risk_solver=status_risk,
        return_constraint_violation=return_violation,
        risk_constraint_shortfall=return_shortfall,
        minimum_volatility_at_user_return=sigma_min,
        primal_tolerance=tolerance,
        optimizer_allocation_violation=allocation_violation,
        elapsed_seconds=time.perf_counter() - started,
    )
    if max(return_violation, return_shortfall, allocation_violation) > tolerance:
        result.update(status="numerical_inconsistency")
        result["diagnostics"]["reason"] = (
            "An optimizer solution violates its disclosed primal tolerance."
        )
        return result
    if delta_mu < -tolerance or delta_sigma < -tolerance:
        result.update(status="numerical_inconsistency")
        result["diagnostics"].update(
            return_shortfall_raw=delta_mu, excess_volatility_raw=delta_sigma
        )
        return result
    result.update(
        status="optimal",
        return_shortfall=max(0.0, delta_mu),
        excess_volatility=max(0.0, delta_sigma),
    )
    return result


def summarize_distances(results: list[dict]) -> dict:
    valid = [r for r in results if r.get("status") == "optimal"]
    failed = len(results) - len(valid)
    output = {"valid_count": len(valid), "failed_count": failed, "results": results}
    for key in ("return_shortfall", "excess_volatility"):
        values = np.asarray([r[key] for r in valid], dtype=float)
        output[key] = (
            {
                "mean": float(values.mean()),
                "median": float(np.median(values)),
                "p5": float(np.quantile(values, 0.05)),
                "p95": float(np.quantile(values, 0.95)),
                "minimum": float(values.min()),
                "maximum": float(values.max()),
            }
            if len(values)
            else None
        )
    return output
