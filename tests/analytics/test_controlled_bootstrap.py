from base64 import b64decode
from dataclasses import replace

import numpy as np
import pytest

from app.analytics.bootstrap import analyze_bootstrap, stationary_indices
from app.analytics.models import OptimizationResult
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.portfolio_controls import analyze_bootstrap_trend_sensitivity, compare_controls

USER = {"1": 0.6, "2": 0.2, "3": 0.2}


def test_selected_my_portfolio_objective_uses_controlled_sample_risk(panel):
    p = controlled_panel(panel)
    result = analyze_bootstrap(
        p,
        bootstrap_iterations=4,
        user_weights=USER,
        objectives=["max_return_at_user_risk"],
    )
    objective = result["objectives"]["max_return_at_user_risk"]
    expected = compare_controls(p, USER, record_history=False)["controlled"]["net"]
    assert (
        objective["original"]["diagnostics"]["training_user_volatility"]
        == expected["annualized_volatility"]
    )
    for record, sample in zip(
        objective["iterations"], result["portfolio_control_samples"], strict=True
    ):
        assert (
            record["diagnostics"]["training_user_volatility"]
            == sample["controlled"]["net"]["annualized_volatility"]
        )
        assert record["diagnostics"]["risk_budget_source"] == "controlled_training_path"


def controlled_panel(panel, mode="downside_vol", basis="nominal"):
    frame = panel.returns.copy()
    rf = np.full(len(frame), 0.001)
    if basis == "real":
        inflation = 0.002 + 0.001 * np.sin(np.arange(len(frame)))
        frame.iloc[:, :] = (1 + frame.values) / (1 + inflation[:, None]) - 1
        rf = (1 + rf) / (1 + inflation) - 1
    hedge = frame.copy()
    hedge["hedge"] = rf
    return replace(
        panel,
        returns=frame,
        risk_free_returns=rf,
        return_basis=basis,
        control_returns=hedge,
        simulation_options={"transaction_fee_rate": 0.002, "rebalancing_interval_months": 12},
        control_config={
            "mode": mode,
            "hedge_weights": {"hedge": 1},
            "downside_vol": {"target_annual": 0.06, "lookback_months": 1},
            "trend": {
                "destination": "hedge",
                "asset_rules": {
                    "1": {"indicator": "momentum", "lookback_months": 3},
                },
            },
        },
    )


@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("mode", ["downside_vol", "trend"])
def test_controlled_bootstrap_train_valid_and_shared_samples(panel, method, basis, mode):
    p = controlled_panel(panel, mode, basis)
    options = dict(
        bootstrap_iterations=4,
        objectives=["gmv"],
        user_weights=USER,
        covariance_method=method,
        random_seed=42,
    )
    result = analyze_bootstrap(p, **options)
    passive = analyze_bootstrap(replace(p, control_config={}), **options)
    assert result["sample_fingerprint"] == passive["sample_fingerprint"]
    assert (
        result["objectives"]["gmv"]["resampled"]["weights"]
        == passive["objectives"]["gmv"]["resampled"]["weights"]
    )
    paired = result["paired_comparison"]
    assert paired["user_portfolio_mode"] == "controlled"
    assert paired["generated_datasets"] == 4
    assert paired["requested_comparisons"] == 2
    assert paired["valid_count"] > 0
    rng = np.random.default_rng(42)
    all_cagr = []
    for i in range(2):
        train = stationary_indices(len(p.values), 12, rng)
        valid = stationary_indices(len(p.values), 12, rng)
        training = compare_controls(p, USER, train, record_history=False)["controlled"]["net"]
        validation_path = compare_controls(
            p, USER, valid, record_history=False, include_wealth=True
        )["controlled"]
        validation = validation_path["net"]
        all_cagr.extend([training["annualized_cagr"], validation["annualized_cagr"]])
        record = paired["results"][i]
        saved = np.exp(np.frombuffer(b64decode(record["wealth_log_f32"]["user"]), dtype="<f4"))
        np.testing.assert_allclose(saved, validation_path["net_wealth"], rtol=2e-6)
        assert "net_wealth" not in result["portfolio_control_samples"][2 * i + 1]["controlled"]
        assert (
            record["diagnostics"]["training_user_volatility"] == training["annualized_volatility"]
        )
        assert record["diagnostics"]["risk_budget_source"] == "controlled_training_path"
        assert record["user"]["annualized_cagr"] == validation["annualized_cagr"]
        assert record["user"]["historical_sharpe_ratio"] == validation["historical_sharpe_ratio"]
        assert record["differences"]["delta_cagr"] == pytest.approx(
            record["optimized"]["annualized_cagr"] - validation["annualized_cagr"]
        )
        assert paired["clouds"][i]["user"]["volatility"] == validation["annualized_volatility"]
    distribution = result["efficiency_robustness"]["distributions"]["annualized_cagr"]
    assert distribution["valid_count"] == 4
    assert distribution["mean"] == pytest.approx(np.mean(all_cagr))


def test_validation_change_cannot_affect_controlled_training(panel, monkeypatch):
    p = controlled_panel(panel)
    train = np.arange(120)
    valid = np.arange(120, 240)

    def run(current):
        draws = iter([train, valid])
        monkeypatch.setattr("app.analytics.bootstrap.stationary_indices", lambda *a: next(draws))
        return analyze_bootstrap(
            current, bootstrap_iterations=2, user_weights=USER, objectives=["gmv"]
        )["paired_comparison"]["results"][0]

    a = run(p)
    frame = p.returns.copy()
    frame.iloc[120:, :] += 0.03
    control = p.control_returns.copy()
    control.iloc[120:, :3] += 0.03
    b = run(replace(p, returns=frame, control_returns=control))
    assert a["weights"] == b["weights"]
    assert (
        a["diagnostics"]["training_user_volatility"] == b["diagnostics"]["training_user_volatility"]
    )
    assert a["user"]["annualized_cagr"] != b["user"]["annualized_cagr"]


def test_control_failure_never_falls_back_to_passive(panel, monkeypatch):
    p = controlled_panel(panel)
    original = compare_controls

    def fail_validation(panel, user, indices=None, **kwargs):
        if indices is not None:
            raise ValueError("controlled simulation failed")
        return original(panel, user, indices, **kwargs)

    monkeypatch.setattr("app.analytics.portfolio_controls.compare_controls", fail_validation)
    result = analyze_bootstrap(p, bootstrap_iterations=4, user_weights=USER, objectives=["gmv"])
    assert result["paired_comparison"]["failed_count"] == 2
    assert all(
        "controlled simulation failed" in x["diagnostics"]["reason"]
        for x in result["paired_comparison"]["results"]
    )
    assert result["objectives"]["gmv"]["status"] == "complete"
    assert result["efficiency_robustness"]["distributions"]["annualized_cagr"]["valid_count"] == 0


def test_controlled_risk_infeasible_is_not_replaced(panel, monkeypatch):
    def infeasible(self, user, *, training_volatility=None):
        return OptimizationResult(
            "risk_infeasible", None, {"training_user_volatility": training_volatility}
        )

    monkeypatch.setattr(PortfolioOptimizer, "max_return_at_user_risk", infeasible)
    result = analyze_bootstrap(
        controlled_panel(panel), bootstrap_iterations=4, user_weights=USER, objectives=["gmv"]
    )
    assert result["paired_comparison"]["failed_count"] == 2
    assert all(
        r["status"] == "risk_infeasible" and r["optimized"] is None
        for r in result["paired_comparison"]["results"]
    )
    assert result["efficiency_robustness"]["distributions"]["annualized_cagr"]["valid_count"] == 4


@pytest.mark.parametrize(
    ("basis", "destination", "interval"),
    [("nominal", "hedge", 4), ("real", "remaining", None)],
)
def test_trend_sensitivity_reuses_every_draw_with_portfolio_and_single_asset_rules(
    panel, monkeypatch, basis, destination, interval
):
    p = controlled_panel(panel, "trend", basis)
    trend = p.control_config["trend"]
    trend["destination"] = destination
    trend["asset_rules"]["2"] = {"indicator": "sma", "lookback_months": 8}
    # The hedge overlaps a managed asset and also contains an external instrument.
    p.control_config["hedge_weights"] = {"2": 0.4, "hedge": 0.6}
    rng = np.random.default_rng(11)
    samples = [stationary_indices(len(p.values), 6, rng) for _ in range(3)]
    remaining = iter(samples)
    monkeypatch.setattr("app.analytics.bootstrap.stationary_indices", lambda *args: next(remaining))
    retained = []
    fee_rate = 0.002
    result = analyze_bootstrap(
        p,
        bootstrap_iterations=len(samples),
        objectives=["gmv"],
        user_weights=USER,
        include_paired_comparison=False,
        transaction_fee_rate=fee_rate,
        rebalancing_interval_months=interval,
        _sample_indices=retained,
    )
    assert list(remaining) == []
    assert "trend_sensitivity" not in result
    assert "sensitivity" not in result["portfolio_controls"]
    np.testing.assert_array_equal(retained, samples)
    sweep = analyze_bootstrap_trend_sensitivity(p, user_weights=USER, _sample_indices=retained)[
        "trend_sensitivity"
    ]
    assert sweep["iterations"] == len(samples)
    assert set(sweep["by_asset"]) == {"1", "2"}
    for asset, series in [(None, sweep["portfolio"]), *sweep["by_asset"].items()]:
        assert np.shape(series) == (36, len(samples), 7)
        weights = USER if asset is None else {asset: 1.0}
        for month, actual in enumerate(series, 1):
            rules = {
                key: {**rule, "lookback_months": month}
                for key, rule in trend["asset_rules"].items()
                if asset is None or asset == key
            }
            varied = replace(
                p, control_config={**p.control_config, "trend": {**trend, "asset_rules": rules}}
            )
            expected = []
            for indices in samples:
                controlled = compare_controls(varied, weights, indices, record_history=False)[
                    "controlled"
                ]
                expected.append(
                    [
                        controlled[side][k]
                        for side in ("net", "gross")
                        for k in ("annualized_cagr", "maximum_drawdown", "annualized_volatility")
                    ]
                    + [
                        fee_rate * controlled["annualized_turnover"]
                        + fee_rate / (1 + fee_rate) * 12 / len(indices)
                    ]
                )
            np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-12)
