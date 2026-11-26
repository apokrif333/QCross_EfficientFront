"""Target feasibility is a resampling outcome, distinct from technical failures."""

import copy

import numpy as np
import pytest

import app.analytics.bootstrap as bootstrap
from app.analytics.constraints import build_constraints
from app.analytics.cross_validation import analyze_cross_validation
from app.analytics.frontier import analyze_frontier
from app.analytics.models import AnalyticsError, OptimizationResult
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.references import real_returns
from app.analytics.splitting import bootstrap_folds


def test_target_is_minimum_and_gmv_is_valid():
    mean, covariance = np.array([0.04, 0.12]), np.diag([0.01, 0.04])
    engine = PortfolioOptimizer(mean, covariance, build_constraints(["A", "B"]))
    gmv = engine.gmv().require_weights()
    low = engine.target_return(0.02).require_weights()
    np.testing.assert_allclose(low, gmv, atol=1e-6)
    assert mean @ low > 0.02
    binding = engine.target_return(0.10).require_weights()
    assert mean @ binding == pytest.approx(0.10, abs=1e-7)
    np.testing.assert_allclose(binding, [0.25, 0.75], atol=1e-6)


def test_feasibility_respects_constraints_and_numerical_tolerance():
    engine = PortfolioOptimizer(
        np.array([0.04, 0.12]), np.eye(2), build_constraints(["A", "B"], {"B": {"max": 0.25}})
    )
    # Raw maximum asset mean is 12%, but the constrained maximum is 6%.
    assert bootstrap._target_solution(engine, 0.08, 0.03).status == "target_infeasible"
    boundary = bootstrap._target_solution(engine, 0.06 + 1e-8, 0.03)
    assert boundary.status == "optimal"
    assert engine.mean @ boundary.weights >= 0.06 + 1e-8 - bootstrap.TARGET_TOLERANCE
    assert bootstrap._target_solution(engine, 0.06 + 1e-6, 0.03).status == "target_infeasible"


def test_default_500_target_generations(panel, monkeypatch):
    actual = bootstrap._validate_settings

    def stop(iterations, block, seed):
        assert (iterations, block, seed) == (500, 12, 42)
        actual(iterations, block, seed)
        raise AnalyticsError("checked defaults")

    monkeypatch.setattr(bootstrap, "_validate_settings", stop)
    with pytest.raises(AnalyticsError, match="checked defaults"):
        bootstrap.analyze_bootstrap(panel, objectives=["target_return"], target_return=0.03)


def test_targets_are_conditional_on_all_folds_being_feasible(panel, monkeypatch):
    original = bootstrap._target_solution
    calls = iter(range(12))

    def one_infeasible(*args):
        if next(calls) == 5:
            return OptimizationResult("target_infeasible", None, {"target_feasible": False})
        return original(*args)

    monkeypatch.setattr(bootstrap, "_target_solution", one_infeasible)
    result = bootstrap.analyze_bootstrap(
        panel, objectives=["target_return"], target_return=-0.2, bootstrap_iterations=3
    )["objectives"]["target_return"]
    assert result["status"] == "complete"
    assert result["target_feasibility_rate"] == pytest.approx(2 / 3)
    assert result["target_infeasible_iterations"] == 1
    assert result["successful_iterations"] == 2
    assert result["iterations"][1]["metrics"] is None
    assert result["distributions"]["annualized_cagr"]["valid_count"] == 2
    assert result["resampled"] is None


@pytest.mark.parametrize("failure", ["estimator", "solver", "feasibility_solver"])
def test_technical_failures_suppress_target_averaging(panel, monkeypatch, failure):
    calls = 0
    if failure == "estimator":
        actual = bootstrap._fit_bootstrap_fold

        def fail(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AnalyticsError("injected sample failure")
            return actual(*args)

        monkeypatch.setattr(bootstrap, "_fit_bootstrap_fold", fail)
    else:
        method = "optimize" if failure == "solver" else "maximum_return"
        actual = getattr(PortfolioOptimizer, method)

        def fail(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:  # the second Fold of the first generation fails
                return OptimizationResult("solver_error", None, {})
            return actual(self, *args, **kwargs)

        monkeypatch.setattr(PortfolioOptimizer, method, fail)
    o = bootstrap.analyze_bootstrap(
        panel, objectives=["target_return"], target_return=-0.2, bootstrap_iterations=20
    )["objectives"]["target_return"]
    assert o["status"] == o["computational_status"] == "incomplete"
    assert o["numerical_failure_iterations"] == 1 and o["target_infeasible_iterations"] == 0
    assert o["target_feasibility_rate"] == pytest.approx(1 if failure == "solver" else 19 / 20)
    assert o["feasibility_unknown_iterations"] == (0 if failure == "solver" else 1)
    assert o["resampled"] is o["stability"] is None


@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
def test_target_constraints_moments_and_shared_indices(panel, basis, method):
    p = copy.deepcopy(panel)
    p.return_basis, p.risk_free_source = basis, "historical_bil"
    p.risk_free_returns = np.linspace(0.001, 0.004, 240)
    if basis == "real":
        inflation = np.linspace(-0.001, 0.004, 240)
        p.returns.iloc[:, :] = real_returns(p.values, inflation)
        p.risk_free_returns = real_returns(p.risk_free_returns, inflation)
    options = dict(
        objectives=["target_return", "gmv", "max_sharpe"],
        target_return=0.03,
        bootstrap_iterations=4,
        covariance_method=method,
        asset_constraints={"1": {"max": 0.5}},
        group_constraints={"Equity": {"max": 0.8}},
    )
    result = bootstrap.analyze_bootstrap(p, **options)
    again = bootstrap.analyze_bootstrap(p, **options)
    assert result["status"] == "complete"
    assert result["sample_fingerprint"] == again["sample_fingerprint"]
    o = result["objectives"]["target_return"]
    reference = again["objectives"]["target_return"]["iterations"]
    for x, y in zip(o["iterations"], reference, strict=True):
        assert x["metrics"] == y["metrics"]
        assert [f["weights"] for f in x["folds"]] == [f["weights"] for f in y["folds"]]
    splits = bootstrap_folds(len(p.values), 4, method)
    for i, record in enumerate(o["iterations"]):
        draws = list(bootstrap.bootstrap_generation(splits, 12, 42, i))
        for fold, (train, _) in zip(record["folds"], draws, strict=True):
            if fold["weights"] is None:
                assert fold["status"] == "target_infeasible"
                continue
            w = np.array(list(fold["weights"].values()))
            assert p.values[train].mean(0) * 12 @ w >= 0.03 - 1e-7
            assert w[0] <= 0.5 + 1e-7 and w[0] + w[2] <= 0.8 + 1e-7
        assert (record["metrics"] is not None) == all(
            fold["weights"] is not None for fold in record["folds"]
        )
    assert result["conditional_bootstrap"] is False
    assert result["covariance_method"] == method


def test_frontier_and_cv_accept_gmv_above_target(panel):
    frontier = analyze_frontier(
        panel, objective="target_return", target_return=-0.2, frontier_points=3
    )
    np.testing.assert_allclose(
        list(frontier["selected_portfolio"]["weights"].values()),
        list(frontier["gmv"]["weights"].values()),
        atol=1e-6,
    )
    target = analyze_cross_validation(panel, objective="target_return", target_return=-0.2)
    gmv = analyze_cross_validation(panel, objective="gmv")
    assert target["status"] == "complete"
    for actual, expected in zip(target["folds"], gmv["folds"], strict=True):
        np.testing.assert_allclose(
            list(actual["weights"].values()), list(expected["weights"].values()), atol=1e-6
        )


@pytest.mark.parametrize("target", [None, float("nan"), float("inf")])
def test_target_required_and_finite(panel, target):
    with pytest.raises(AnalyticsError, match="finite annual target_return"):
        bootstrap.analyze_bootstrap(panel, objectives=["target_return"], target_return=target)
