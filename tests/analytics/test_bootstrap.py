import numpy as np
import pytest

from app.analytics.bootstrap import (
    analyze_bootstrap,
    analyze_resampled_frontier,
    bootstrap_weight_statistics,
    stationary_indices,
)
from app.analytics.constraints import build_constraints
from app.analytics.estimators import estimate_covariance
from app.analytics.models import OptimizationResult
from app.analytics.optimizer import PortfolioOptimizer


def test_stationary_geometric_mechanism_and_wrapping():
    class FakeRng:
        def integers(self, upper):
            assert upper == 5
            return 4

        def geometric(self, probability):
            assert probability == 1 / 12
            return 3

    np.testing.assert_array_equal(stationary_indices(5, 12, FakeRng()), [4, 0, 1, 4, 0])
    rng = np.random.default_rng(42)
    geometric = rng.geometric(1 / 12, size=100000)
    assert geometric.mean() == pytest.approx(12, abs=0.1)
    for length in [3, 12, 24]:
        indices = stationary_indices(240, length, np.random.default_rng(42))
        assert len(indices) == 240 and indices.min() >= 0 and indices.max() < 240
        np.testing.assert_array_equal(
            indices, stationary_indices(240, length, np.random.default_rng(42))
        )


def test_joint_resampling(panel):
    indices = stationary_indices(240, 12, np.random.default_rng(7))
    sample = panel.values[indices]
    for i, index in enumerate(indices):
        np.testing.assert_array_equal(sample[i], panel.values[index])


def test_bootstrap_statistics_independent_manual_calculation():
    weights = np.array([[0.2, 0.8], [0.4, 0.6], [0.6, 0.4]])
    original = np.array([0.3, 0.7])
    rules = build_constraints(["A", "B"], {"A": {"min": 0.2, "max": 0.6}})
    result = bootstrap_weight_statistics(weights, original, rules)
    asset = result["assets"]["A"]
    assert asset["mean"] == pytest.approx(0.4)
    assert asset["median"] == pytest.approx(0.4)
    assert asset["standard_deviation"] == pytest.approx(np.std(weights[:, 0]))
    assert asset["p5"] == pytest.approx(0.22) and asset["p95"] == pytest.approx(0.58)
    assert asset["lower_bound_frequency"] == pytest.approx(1 / 3)
    assert asset["upper_bound_frequency"] == pytest.approx(1 / 3)
    assert result["allocation_distance"]["mean"] == pytest.approx((0.1 + 0.1 + 0.3) / 3)


def test_bootstrap_resampled_constraints_and_reproducibility(panel):
    options = {
        "bootstrap_iterations": 12,
        "random_seed": 12,
        "group_constraints": {"Equity": {"max": 0.65}, "US Stocks": {"max": 0.3}},
    }
    result = analyze_bootstrap(panel, **options)
    second = analyze_bootstrap(panel, **options)
    assert (
        result["status"] == "complete"
        and result["sample_fingerprint"] == second["sample_fingerprint"]
    )
    for objective in ["gmv", "max_sharpe"]:
        output = result["objectives"][objective]
        assert output["successful_iterations"] == 12 and output["failed_iterations"] == 0
        vectors = np.array(
            [[record["weights"][a] for a in panel.assets] for record in output["iterations"]]
        )
        average = np.array([output["resampled"]["weights"][a] for a in panel.assets])
        np.testing.assert_allclose(average, vectors.mean(0))
        assert np.all(vectors[:, 0] <= 0.3 + 1e-7) and np.all(
            vectors[:, 0] + vectors[:, 2] <= 0.65 + 1e-7
        )
        assert average.sum() == pytest.approx(1)
        assert (
            output["resampled"]["weights"]
            == second["objectives"][objective]["resampled"]["weights"]
        )
        expected = panel.values.mean(0) * 12 @ average
        assert output["resampled"]["metrics"]["expected_return"] == pytest.approx(expected)
    other = analyze_bootstrap(
        panel, bootstrap_iterations=12, covariance_method="ledoit_wolf", random_seed=12
    )
    assert other["sample_fingerprint"] == result["sample_fingerprint"]


def test_conditional_cv_shrinkage_is_selected_once(panel, monkeypatch):
    import app.analytics.estimators as estimators

    original, calls = estimators.select_shrinkage, []

    def spy(*args):
        calls.append(args[0].copy())
        return original(*args)

    monkeypatch.setattr(estimators, "select_shrinkage", spy)
    result = analyze_bootstrap(
        panel, bootstrap_iterations=4, covariance_method="cv_linear", objectives=["gmv"]
    )
    assert len(calls) == 1 and result["conditional_bootstrap"] is True
    assert result["hyperparameter_uncertainty_included"] is False


def test_resampled_frontier_matches_independent_shared_gamma_average(panel):
    aversions = [50.0, 5.0, 0.5]
    result = analyze_resampled_frontier(
        panel, bootstrap_iterations=4, frontier_points=3, risk_aversions=aversions
    )
    assert result["status"] == "complete"
    rng = np.random.default_rng(42)
    collected = [[] for _ in aversions]
    constraints = build_constraints(panel.assets)
    for _ in range(4):
        sample = panel.values[stationary_indices(240, 12, rng)]
        engine = PortfolioOptimizer(
            sample.mean(0) * 12, estimate_covariance(sample).matrix * 12, constraints
        )
        for i, gamma in enumerate(aversions):
            collected[i].append(engine.utility(1 / gamma).require_weights())
    for point, vectors in zip(result["frontier"], collected, strict=True):
        np.testing.assert_allclose(list(point["weights"].values()), np.mean(vectors, axis=0))
    assert result["risk_aversions"] == aversions


@pytest.mark.parametrize("failures,expected", [(1, "complete"), (2, "incomplete")])
def test_individual_failures_and_95_percent_gate(panel, monkeypatch, failures, expected):
    actual, calls = PortfolioOptimizer.optimize, 0

    def fail(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if 2 <= calls <= failures + 1:  # first call is the original portfolio
            return OptimizationResult("injected_failure", None, {"reason": "test failure"})
        return actual(self, *args, **kwargs)

    monkeypatch.setattr(PortfolioOptimizer, "optimize", fail)
    result = analyze_bootstrap(panel, bootstrap_iterations=20, objectives=["gmv"])
    output = result["objectives"]["gmv"]
    assert result["status"] == expected and output["failed_iterations"] == failures
    assert len(output["iterations"]) == 20 and len(output["failures"]) == failures
    assert (output["resampled"] is None) == (expected == "incomplete")


def test_estimator_failure_is_recorded_for_every_objective(panel):
    panel.returns["2"] = panel.returns["1"]
    # Original nonlinear fitting itself must explicitly fail rather than switch estimator.
    from app.analytics.models import AnalyticsError

    with pytest.raises(AnalyticsError, match="singular"):
        analyze_bootstrap(panel, bootstrap_iterations=3, covariance_method="nonlinear")


def test_failed_sample_estimation_is_not_silently_excluded(panel, monkeypatch):
    import app.analytics.bootstrap as bootstrap
    from app.analytics.models import AnalyticsError

    actual, calls = bootstrap._sample_estimate, 0

    def fail(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AnalyticsError("injected sample estimator failure")
        return actual(*args)

    monkeypatch.setattr(bootstrap, "_sample_estimate", fail)
    result = analyze_bootstrap(panel, bootstrap_iterations=4)
    assert result["status"] == "incomplete"
    for objective in result["objectives"].values():
        assert objective["failed_iterations"] == 1
        assert objective["failures"][0]["iteration"] == 1
        assert objective["resampled"] is None
    frontier = analyze_resampled_frontier(panel, frontier_points=3, bootstrap_iterations=3)
    assert frontier["status"] == "complete"


def test_resampled_frontier_failure_gate(panel, monkeypatch):
    actual, calls = PortfolioOptimizer.utility, 0

    def fail(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            return OptimizationResult("injected_failure", None, {})
        return actual(*args)

    monkeypatch.setattr(PortfolioOptimizer, "utility", fail)
    result = analyze_resampled_frontier(panel, frontier_points=3, bootstrap_iterations=4)
    assert result["status"] == "incomplete" and result["frontier"] == []
    assert result["failed_iterations"] == 1
    assert result["failures"][0]["failed_points"] == [1]
