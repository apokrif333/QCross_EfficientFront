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


def test_default_thousand_target_iterations(panel):
    result = bootstrap.analyze_bootstrap(panel, objectives=["target_return"], target_return=0.03)
    output = result["objectives"]["target_return"]
    assert result["status"] == "complete"
    assert output["requested_iterations"] == len(output["iterations"]) == 1000
    assert result["expected_block_length"] == 12 and result["random_seed"] == 42
    assert output["successful_iterations"] + output["target_infeasible_iterations"] == 1000
    assert output["target_feasibility_rate"] == output["successful_iterations"] / 1000


def test_original_solver_failure_suppresses_aggregate(panel, monkeypatch):
    actual, calls = PortfolioOptimizer.maximum_return, 0

    def fail(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            return OptimizationResult("solver_error", None, {})
        return actual(self)

    monkeypatch.setattr(PortfolioOptimizer, "maximum_return", fail)
    o = bootstrap.analyze_bootstrap(
        panel, objectives=["target_return"], target_return=-0.2, bootstrap_iterations=3
    )["objectives"]["target_return"]
    assert o["original"]["status"] == "solver_failed"
    assert o["status"] == "incomplete" and o["target_feasibility_rate"] == 1
    assert o["numerical_failure_iterations"] == 0  # all samples succeeded
    assert o["resampled"] is o["stability"] is None


@pytest.fixture
def regime_panel(panel):
    rng = np.random.default_rng(31)
    panel.returns.iloc[:, 0] = rng.normal(0.002, 0.001, 240)
    panel.returns.iloc[:, 1] = np.repeat([0.03, 0.05, -0.02], [60, 60, 120]) + rng.normal(
        0, 0.004, 240
    )
    panel.returns.iloc[:, 2] = rng.normal(0.001, 0.002, 240)
    return panel


def controlled_samples(monkeypatch):
    indices = [
        np.tile(np.arange(60), 4),
        np.tile(np.arange(60, 120), 4),
        np.tile(np.arange(120, 240), 2),
    ]
    calls = iter(indices)
    monkeypatch.setattr(bootstrap, "stationary_indices", lambda *args: next(calls))
    return indices


def test_conditional_statistics_and_original_target_shortfall(regime_panel, monkeypatch):
    controlled_samples(monkeypatch)
    result = bootstrap.analyze_bootstrap(
        regime_panel, objectives=["target_return"], target_return=0.10, bootstrap_iterations=3
    )
    o = result["objectives"]["target_return"]
    assert result["status"] == o["computational_status"] == "complete"
    assert o["target_feasibility_rate"] == pytest.approx(2 / 3)
    assert o["target_infeasible_iterations"] == 1
    assert o["numerical_failure_iterations"] == o["failed_iterations"] == 0
    assert o["successful_iterations"] == 2
    assert [r["status"] for r in o["iterations"]] == ["optimal", "optimal", "target_infeasible"]
    vectors = np.array([list(r["weights"].values()) for r in o["iterations"] if r["weights"]])
    np.testing.assert_allclose(list(o["resampled"]["weights"].values()), vectors.mean(0))
    for i, asset in enumerate(regime_panel.assets):
        stats = o["stability"]["assets"][asset]
        assert stats["p5"] == pytest.approx(np.quantile(vectors[:, i], 0.05))
        assert stats["p95"] == pytest.approx(np.quantile(vectors[:, i], 0.95))
        assert stats["median"] == pytest.approx(np.median(vectors[:, i]))
    evaluation = o["resampled_target_evaluation"]
    assert evaluation["expected_return"] == pytest.approx(
        regime_panel.values.mean(0) * 12 @ vectors.mean(0)
    )
    assert not evaluation["meets_target"] and not evaluation["target_guaranteed"]
    assert evaluation["target_shortfall"] == pytest.approx(0.10 - evaluation["expected_return"])
    assert o["original_target_feasible"] is True


@pytest.mark.parametrize("target,feasible", [(1.0, 0), (0.25, 2)])
def test_original_infeasible_and_zero_feasible_samples(regime_panel, monkeypatch, target, feasible):
    controlled_samples(monkeypatch)
    result = bootstrap.analyze_bootstrap(
        regime_panel, objectives=["target_return"], target_return=target, bootstrap_iterations=3
    )
    o = result["objectives"]["target_return"]
    assert o["status"] == "complete" and o["original_target_feasible"] is False
    assert o["original"]["status"] == "target_infeasible"
    assert o["original"]["weights"] is None and o["original"]["metrics"] is None
    assert o["target_feasible_iterations"] == feasible
    assert o["target_feasibility_rate"] == pytest.approx(feasible / 3)
    assert o["numerical_failure_iterations"] == 0
    if feasible == 0:
        assert o["resampled"] is o["stability"] is None
    else:
        assert o["resampled"] is not None
        assert o["stability"]["allocation_distance"] is None
        assert all(s["original"] is None for s in o["stability"]["assets"].values())


@pytest.mark.parametrize("failure", ["estimator", "solver", "feasibility_solver"])
def test_technical_failures_suppress_target_averaging(panel, monkeypatch, failure):
    calls = 0
    if failure == "estimator":
        actual = bootstrap._sample_estimate

        def fail(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AnalyticsError("injected sample failure")
            return actual(*args)

        monkeypatch.setattr(bootstrap, "_sample_estimate", fail)
    else:
        method = "optimize" if failure == "solver" else "maximum_return"
        actual = getattr(PortfolioOptimizer, method)

        def fail(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:  # original succeeds, first sample fails
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
    assert o["resampled"]["weights"] == again["objectives"]["target_return"]["resampled"]["weights"]
    rng = np.random.default_rng(42)
    for record in o["iterations"]:
        indices = bootstrap.stationary_indices(240, 12, rng)
        sample = p.values[indices]
        if record["status"] == "target_infeasible":
            rules = build_constraints(
                p.assets, options["asset_constraints"], options["group_constraints"], p.groups
            )
            maximum = (
                PortfolioOptimizer(sample.mean(0) * 12, np.eye(3), rules)
                .maximum_return()
                .require_weights()
            )
            assert sample.mean(0) * 12 @ maximum < 0.03 - 1e-7
            continue
        w = np.array(list(record["weights"].values()))
        assert sample.mean(0) * 12 @ w >= 0.03 - 1e-7
        assert w[0] <= 0.5 + 1e-7 and w[0] + w[2] <= 0.8 + 1e-7
        diff = sample @ w - p.risk_free_returns[indices]
        assert record["metrics"]["historical_sharpe_ratio"] == pytest.approx(
            np.sqrt(12) * diff.mean() / diff.std(ddof=1)
        )
    assert result["conditional_bootstrap"] == (method == "cv_linear")
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
