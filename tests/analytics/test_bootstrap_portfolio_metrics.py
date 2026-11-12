from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from app.analytics.bootstrap import analyze_bootstrap, stationary_indices
from app.analytics.bootstrap_metrics import PORTFOLIO_METRICS, fixed_portfolio_metrics
from app.analytics.models import AnalyticsError, ReturnPanel
from tests.analytics.test_portfolio_simulator import annual_returns


def manual_metrics(monthly, rf):
    wealth = np.r_[1.0, np.cumprod(1 + monthly)]
    differential = monthly - rf
    return {
        "annualized_cagr": wealth[-1] ** (12 / len(monthly)) - 1,
        "annualized_arithmetic_return": monthly.mean() * 12,
        "annualized_volatility": monthly.std(ddof=1) * np.sqrt(12),
        "maximum_drawdown": -(wealth / np.maximum.accumulate(wealth) - 1).min(),
        "historical_sharpe_ratio": np.sqrt(12) * differential.mean() / differential.std(ddof=1),
        "terminal_wealth": wealth[-1],
    }


def test_known_fixed_weight_path_and_time_varying_risk_free():
    values = np.array([[0.1, 0.02], [-0.2, -0.03], [0.05, 0.01]])
    weights = np.array([0.6, 0.4])
    rf = np.array([0.012, -0.003, 0.004])
    expected = manual_metrics(annual_returns(values, weights), rf)
    result = fixed_portfolio_metrics(values, weights, rf)
    for metric, value in expected.items():
        assert result[metric] == pytest.approx(value, abs=1e-12)


@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
def test_full_sample_metrics_use_exact_existing_indices_and_aligned_bil(
    panel, basis, method, monkeypatch
):
    import app.analytics.bootstrap as module

    actual, sampled_lengths = module.stationary_indices, []

    def spy(observations, *args):
        sampled_lengths.append(observations)
        return actual(observations, *args)

    monkeypatch.setattr(module, "stationary_indices", spy)
    rf = 0.001 + panel.values[:, 0] * 0.1
    returns = panel.returns.copy()
    if basis == "real":
        inflation = np.linspace(-0.001, 0.004, len(rf))
        returns = (1 + returns).div(1 + inflation, axis=0) - 1
        rf = (1 + rf) / (1 + inflation) - 1
    panel = replace(
        panel,
        returns=returns,
        risk_free_returns=rf,
        risk_free_source="historical_bil",
        return_basis=basis,
    )
    weights = np.array([0.2, 0.3, 0.5])
    settings = {
        "bootstrap_iterations": 4,
        "objectives": ["gmv"],
        "random_seed": 19,
        "covariance_method": method,
    }
    baseline = analyze_bootstrap(panel, include_efficiency_robustness=False, **settings)
    result = analyze_bootstrap(
        panel, user_weights=dict(zip(panel.assets, weights, strict=True)), **settings
    )
    assert result["sample_fingerprint"] == baseline["sample_fingerprint"]
    assert sampled_lengths == [len(panel.values)] * 8
    assert [r["weights"] for r in result["objectives"]["gmv"]["iterations"]] == [
        r["weights"] for r in baseline["objectives"]["gmv"]["iterations"]
    ]
    robustness = result["efficiency_robustness"]
    rng = np.random.default_rng(19)
    expected_records = []
    for record in robustness["results"]:
        indices = stationary_indices(len(panel.values), 12, rng)
        expected = manual_metrics(annual_returns(panel.values[indices], weights), rf[indices])
        assert record["observations"] == len(panel.values)
        for key, value in expected.items():
            assert record["portfolio_metrics"][key] == pytest.approx(value)
        expected_records.append(expected)
    for key, distribution in robustness["distributions"].items():
        observations = (
            [r[key] for r in expected_records]
            if key in PORTFOLIO_METRICS
            else [r[key] for r in robustness["results"] if r["status"] == "optimal"]
        )
        for name, expected in {
            "mean": np.mean(observations),
            "median": np.median(observations),
            "p5": np.quantile(observations, 0.05),
            "p95": np.quantile(observations, 0.95),
            "minimum": np.min(observations),
            "maximum": np.max(observations),
        }.items():
            assert distribution[name] == pytest.approx(expected)
        assert distribution["horizon_months"] == 240
        assert distribution["horizon_years"] == 20
    assert robustness["horizon"]["sample_length_policy"] == "full_selected_return_panel"


@pytest.mark.parametrize("months", [180, 360])
def test_bootstrap_horizon_is_full_selected_history_not_rolling_window(months):
    values = np.random.default_rng(9).normal(0.005, 0.02, (months, 2))
    panel = ReturnPanel(
        pd.DataFrame(
            values, index=pd.period_range("1990-01", periods=months, freq="M"), columns=["1", "2"]
        ),
        ["1", "2"],
    )
    result = analyze_bootstrap(
        panel, bootstrap_iterations=2, objectives=["gmv"], user_weights={"1": 0.5, "2": 0.5}
    )
    assert result["sample_length"] == months
    robustness = result["efficiency_robustness"]
    assert all(r["observations"] == months for r in robustness["results"])
    assert all(d["horizon_months"] == months for d in robustness["distributions"].values())


def test_path_metrics_survive_frontier_and_estimator_failures(panel, monkeypatch):
    import app.analytics.bootstrap as module

    settings = {
        "bootstrap_iterations": 3,
        "include_paired_comparison": False,
        "objectives": ["gmv"],
        "user_weights": {"1": 1.0},
    }

    def fail(*args, **kwargs):
        raise AnalyticsError("injected failure")

    monkeypatch.setattr(module, "efficiency_distance", fail)
    result = analyze_bootstrap(panel, **settings)
    robustness = result["efficiency_robustness"]
    assert robustness["failed_count"] == 3
    for key in PORTFOLIO_METRICS:
        assert robustness["distributions"][key]["valid_count"] == 3
    assert robustness["distributions"]["return_shortfall"]["valid_count"] == 0
    monkeypatch.setattr(module, "_sample_estimate", fail)
    result = analyze_bootstrap(panel, **settings)
    assert result["status"] == "incomplete"
    assert result["efficiency_robustness"]["distributions"]["annualized_cagr"]["valid_count"] == 3


def test_zero_differential_volatility_and_complete_loss_are_explicit(panel):
    weights = np.full(3, 1 / 3)
    panel = replace(
        panel, risk_free_returns=panel.values @ weights, risk_free_source="historical_bil"
    )
    result = analyze_bootstrap(
        panel,
        bootstrap_iterations=3,
        include_paired_comparison=False,
        objectives=["gmv"],
        rebalancing_interval_months=1,
        user_weights=dict(zip(panel.assets, weights, strict=True)),
    )
    sharpe = result["efficiency_robustness"]["distributions"]["historical_sharpe_ratio"]
    assert sharpe["valid_count"] == 0 and sharpe["unavailable_count"] == 3
    assert sharpe["mean"] is None and sharpe["minimum"] is None
    loss = fixed_portfolio_metrics(np.array([[0.1], [-1.0], [0.5]]), np.ones(1), np.zeros(3))
    assert loss["annualized_cagr"] == -1
    assert loss["maximum_drawdown"] == 1
    assert loss["terminal_wealth"] == 0


def test_terminal_wealth_overflow_is_null_without_overflowing_cagr_or_drawdown():
    metrics = fixed_portfolio_metrics(np.ones((2000, 1)), np.ones(1), np.zeros(2000))
    assert metrics["terminal_wealth"] is None
    assert metrics["annualized_cagr"] == pytest.approx(4095)
    assert metrics["maximum_drawdown"] == 0
