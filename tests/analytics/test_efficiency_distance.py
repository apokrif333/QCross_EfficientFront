import numpy as np
import pytest

from app.analytics.constraints import build_constraints
from app.analytics.efficiency_distance import efficiency_distance
from app.analytics.estimators import estimate_covariance


def test_fixed_portfolio_has_positive_distances_when_dominated():
    values = np.tile([0.05 / 12, 0.15 / 12, 0.08 / 12], (180, 1))
    covariance = np.diag([0.01, 0.02, 0.09]) / 12
    constraints = build_constraints(["a", "b", "c"])
    result = efficiency_distance(values, covariance, np.array([0, 0, 1.0]), constraints)
    assert result["status"] == "optimal"
    assert result["return_shortfall"] == pytest.approx(0.07, abs=1e-6)
    assert result["excess_volatility"] == pytest.approx(
        0.3 - np.sqrt(1 / (100 + 50 + 1 / 0.09)), abs=1e-6
    )
    assert result["user_weights"] == {"a": 0.0, "b": 0.0, "c": 1.0}


def test_efficient_portfolio_has_zero_distances():
    values = np.tile([0.05 / 12, 0.15 / 12], (180, 1))
    covariance = np.diag([0.01, 0.04]) / 12
    constraints = build_constraints(["a", "b"])
    result = efficiency_distance(values, covariance, np.array([0.0, 1.0]), constraints)
    assert result["status"] == "optimal"
    assert result["return_shortfall"] < 1e-6
    assert result["excess_volatility"] < 1e-6


def test_infeasible_fixed_user_allocation_is_not_comparable():
    constraints = build_constraints(["a", "b"], {"a": {"max": 0.5}})
    result = efficiency_distance(
        np.ones((120, 2)) * 0.01,
        np.eye(2) / 12,
        np.array([0.8, 0.2]),
        constraints,
    )
    assert result["status"] == "not_comparable"
    assert result["return_shortfall"] is None


def test_negative_expected_returns_and_zero_covariance():
    rules = build_constraints(["a", "b"])
    negative = efficiency_distance(
        np.full((120, 2), -0.005),
        np.diag([0.01, 0.04]) / 12,
        np.array([1.0, 0]),
        rules,
    )
    assert negative["status"] == "optimal"
    assert negative["return_shortfall"] == pytest.approx(0, abs=1e-7)
    assert negative["excess_volatility"] == pytest.approx(0.1 - np.sqrt(0.008), abs=1e-6)
    degenerate = efficiency_distance(
        np.tile([0.05 / 12, 0.15 / 12], (120, 1)),
        np.zeros((2, 2)),
        np.array([1.0, 0]),
        rules,
    )
    assert degenerate["status"] == "optimal"
    assert degenerate["return_shortfall"] == pytest.approx(0.1, abs=1e-6)
    assert degenerate["excess_volatility"] == 0


def test_solver_failure_is_explicit(monkeypatch):
    import cvxpy as cp

    rules = build_constraints(["a", "b"])

    def fail(*args, **kwargs):
        raise cp.SolverError("injected solver failure")

    monkeypatch.setattr(cp.Problem, "solve", fail)
    result = efficiency_distance(
        np.full((120, 2), 0.005), np.eye(2) / 12, np.array([0.5, 0.5]), rules
    )
    assert result["status"] == "solver_failed"
    assert result["return_shortfall"] is None
    assert "injected solver failure" in result["diagnostics"]["return_error"]


def test_materially_negative_gap_is_reported_without_clipping(monkeypatch):
    import app.analytics.efficiency_distance as module

    actual = module._solve

    def inconsistent(problem):
        status = actual(problem)
        if problem.objective.NAME == "maximize":
            problem._value = -100
        return status

    monkeypatch.setattr(module, "_solve", inconsistent)
    result = efficiency_distance(
        np.full((120, 2), 0.005),
        np.eye(2) / 12,
        np.array([0.5, 0.5]),
        build_constraints(["a", "b"]),
    )
    assert result["status"] == "numerical_inconsistency"
    assert result["return_shortfall"] is None
    assert result["diagnostics"]["return_shortfall_raw"] < -1


def test_nonfinite_weights_and_indefinite_covariance_are_rejected():
    rules = build_constraints(["a", "b"])
    result = efficiency_distance(
        np.full((120, 2), 0.005), np.eye(2), np.array([np.nan, 0.5]), rules
    )
    assert result["status"] == "not_comparable"
    assert "user_expected_return" not in result
    result = efficiency_distance(
        np.full((120, 2), 0.005),
        np.array([[1.0, 2], [2, 1]]),
        np.array([0.5, 0.5]),
        rules,
    )
    assert result["status"] == "numerical_failure"


def test_group_constraints_and_fixed_weights_are_respected():
    rules = build_constraints(
        ["a", "b", "c"],
        group_bounds={"equities": {"max": 0.5}},
        memberships={"equities": ["b", "c"]},
    )
    weights = np.array([0.5, 0.3, 0.2])
    original = weights.copy()
    result = efficiency_distance(
        np.tile([0.03 / 12, 0.2 / 12, 0.1 / 12], (120, 1)),
        np.diag([0.01, 0.02, 0.03]) / 12,
        weights,
        rules,
    )
    assert result["status"] == "optimal"
    np.testing.assert_array_equal(weights, original)
    assert result["diagnostics"]["optimizer_allocation_violation"] <= 1e-7


@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
def test_distances_use_each_covariance_estimator_and_return_basis(panel, method, basis):
    values = panel.values.copy()
    if basis == "real":
        inflation = np.linspace(-0.002, 0.003, len(values))[:, None]
        values = (1 + values) / (1 + inflation) - 1
    estimate = estimate_covariance(values, method, folds=4)
    weights = np.full(len(panel.assets), 1 / len(panel.assets))
    result = efficiency_distance(
        values,
        estimate.matrix,
        weights,
        build_constraints(panel.assets),
        estimator_parameters=estimate.parameters,
        covariance_diagnostics=estimate.diagnostics,
    )
    assert result["status"] == "optimal"
    assert np.isfinite(result["user_expected_return"])
    assert np.isfinite(result["user_volatility"])
    assert result["diagnostics"]["covariance_diagnostics"] == estimate.diagnostics
