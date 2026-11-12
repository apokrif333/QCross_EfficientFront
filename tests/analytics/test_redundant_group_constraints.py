import cvxpy as cp
import numpy as np
import pytest

from app.analytics.constraints import build_constraints
from app.analytics.optimizer import PortfolioOptimizer


def test_default_group_does_not_change_my_portfolio_solution():
    assets = ["1", "2", "3"]
    mean = np.array([0.05, 0.08, 0.12])
    covariance = np.array([[0.02, 0.004, 0.002], [0.004, 0.04, 0.008], [0.002, 0.008, 0.09]])
    user = np.array([0.4, 0.35, 0.25])
    baseline = build_constraints(assets)
    grouped = build_constraints(
        assets, group_bounds={"all": {"min": 0, "max": 1}}, memberships={"all": assets}
    )
    assert len(grouped.cvx(cp.Variable(3))) == len(baseline.cvx(cp.Variable(3)))
    plain = (
        PortfolioOptimizer(mean, covariance, baseline)
        .max_return_at_user_risk(user)
        .require_weights()
    )
    actual = (
        PortfolioOptimizer(mean, covariance, grouped)
        .max_return_at_user_risk(user)
        .require_weights()
    )
    np.testing.assert_allclose(actual, plain, atol=1e-7)
    assert actual @ covariance @ actual <= user @ covariance @ user + 1e-7
    assert grouped.group_names == ["all"]
    assert grouped.violation(actual) <= 1e-7


@pytest.mark.parametrize("homogeneous", [False, True])
def test_nontrivial_group_bounds_are_enforced(homogeneous):
    groups = {"stock": ["1", "2"], "all": ["1", "2", "3"]}
    constraints = build_constraints(
        ["1", "2", "3"],
        group_bounds={"stock": {"min": 0.3, "max": 0.4}, "all": {"min": 0, "max": 1}},
        memberships=groups,
    )
    weights = cp.Variable(3)
    scale = cp.Variable(nonneg=True) if homogeneous else 1
    rules = constraints.cvx(weights, scale)
    assert len(rules) == 5
    problem = cp.Problem(
        cp.Maximize(weights[0] + weights[1]), rules + ([scale == 1] if homogeneous else [])
    )
    problem.solve(solver="CLARABEL")
    assert problem.status == cp.OPTIMAL
    assert sum(weights.value[:2]) == pytest.approx(0.4, abs=1e-7)
    assert constraints.violation(weights.value) <= 1e-7
