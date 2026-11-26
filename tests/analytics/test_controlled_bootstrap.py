from base64 import b64decode
from dataclasses import replace

import numpy as np
import pytest

from app.analytics.bootstrap import analyze_bootstrap, bootstrap_generation
from app.analytics.models import OptimizationResult
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.portfolio_controls import analyze_bootstrap_trend_sensitivity, compare_controls
from app.analytics.splitting import bootstrap_folds

USER = {"1": 0.6, "2": 0.2, "3": 0.2}


def test_selected_my_portfolio_objective_uses_controlled_sample_risk(panel):
    p = controlled_panel(panel)
    result = analyze_bootstrap(
        p,
        bootstrap_iterations=4,
        user_weights=USER,
        objectives=["max_return_at_user_risk"],
        capital_gains_tax_rate=0.1,
        transaction_fee_rate=0.002,
    )
    objective = result["objectives"]["max_return_at_user_risk"]
    assert objective["original"]["weights"] is None
    splits = bootstrap_folds(len(p.values), 4, "sample")
    for iteration, row in enumerate(objective["iterations"]):
        draws = list(bootstrap_generation(splits, 12, 42, iteration))
        for fold, (train, _), (source_train, _) in zip(row["folds"], draws, splits, strict=True):
            expected = compare_controls(
                p,
                USER,
                train,
                record_history=False,
                synthetic=True,
                calendar_start=int(source_train[0]),
            )["controlled"]["net"]
            assert (
                fold["diagnostics"]["training_user_volatility"] == expected["annualized_volatility"]
            )
            assert fold["diagnostics"]["risk_budget_source"] == "controlled_training_path"


def controlled_panel(panel, mode="downside_vol", basis="nominal"):
    frame = panel.returns.copy()
    rf = np.full(len(frame), 0.001)
    signal_returns = None
    inflation_factors = None
    if basis == "real":
        signal_returns = frame.assign(hedge=rf)
        inflation = 0.002 + 0.001 * np.sin(np.arange(len(frame)))
        inflation_factors = 1 + inflation
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
        control_signal_returns=signal_returns,
        inflation_factors=inflation_factors,
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
    for a, b in zip(
        result["objectives"]["gmv"]["iterations"],
        passive["objectives"]["gmv"]["iterations"],
        strict=True,
    ):
        assert a["metrics"] == b["metrics"]
        assert [f["weights"] for f in a["folds"]] == [f["weights"] for f in b["folds"]]
    paired = result["paired_comparison"]
    assert paired["user_portfolio_mode"] == "controlled"
    assert paired["generated_datasets"] == paired["requested_comparisons"] == 4
    splits = bootstrap_folds(len(p.values), 4, method)
    all_cagr = []
    for i in range(4):
        valid = np.concatenate([v for _, v in bootstrap_generation(splits, 12, 42, i)])
        expected = compare_controls(p, USER, valid, record_history=False, include_wealth=True)
        validation_path = expected["controlled"]
        validation = validation_path["net"]
        all_cagr.append(validation["annualized_cagr"])
        record = paired["results"][i]
        saved = np.exp(np.frombuffer(b64decode(record["wealth_log_f32"]["user"]), dtype="<f4"))
        np.testing.assert_allclose(saved, validation_path["net_wealth"], rtol=2e-6)
        sample = result["portfolio_control_samples"][i]
        assert sample["indices_sha256"] == record["validation_sha256"]
        assert "net_wealth" not in sample["controlled"]
        assert sample["baseline"] == expected["baseline"]
        assert record["user"]["annualized_cagr"] == validation["annualized_cagr"]
        assert record["user"]["historical_sharpe_ratio"] == validation["historical_sharpe_ratio"]
        if record["optimized"] is not None:
            assert record["differences"]["delta_cagr"] == pytest.approx(
                record["optimized"]["annualized_cagr"] - validation["annualized_cagr"]
            )
        assert paired["clouds"][i]["user"]["volatility"] == validation["annualized_volatility"]
    distribution = result["efficiency_robustness"]["distributions"]["annualized_cagr"]
    assert distribution["valid_count"] == 4
    assert distribution["mean"] == pytest.approx(np.mean(all_cagr))


def test_validation_change_cannot_affect_controlled_training(panel):
    p = controlled_panel(panel)

    def run(current):
        return analyze_bootstrap(
            current,
            bootstrap_iterations=1,
            user_weights=USER,
            objectives=["max_return_at_user_risk"],
            covariance_method="cv_linear",
        )

    a = run(p)
    frame, control, rf = p.returns.copy(), p.control_returns.copy(), p.risk_free_returns.copy()
    frame.iloc[:60, :] += 0.03
    control.iloc[:60, :] += 0.03
    rf[:60] += 0.01
    b = run(replace(p, returns=frame, control_returns=control, risk_free_returns=rf))
    objective = "max_return_at_user_risk"
    assert a["sample_fingerprint"] == b["sample_fingerprint"]
    first_fold = [x["objectives"][objective]["iterations"][0]["folds"][0] for x in (a, b)]
    assert first_fold[0]["weights"] == first_fold[1]["weights"]
    assert first_fold[0]["status"] == first_fold[1]["status"]
    assert (
        first_fold[0]["diagnostics"]["training_user_volatility"]
        == first_fold[1]["diagnostics"]["training_user_volatility"]
    )
    assert a["generations"][0]["folds"][0] == b["generations"][0]["folds"][0]
    assert (
        a["paired_comparison"]["results"][0]["user"] != b["paired_comparison"]["results"][0]["user"]
    )


def test_control_failure_never_falls_back_to_passive(panel, monkeypatch):
    p = controlled_panel(panel)
    original = compare_controls

    def fail_validation(panel, user, indices=None, **kwargs):
        if indices is not None and len(indices) == len(panel.values):
            raise ValueError("controlled simulation failed")
        return original(panel, user, indices, **kwargs)

    monkeypatch.setattr("app.analytics.bootstrap.compare_controls", fail_validation)
    result = analyze_bootstrap(p, bootstrap_iterations=4, user_weights=USER, objectives=["gmv"])
    assert result["paired_comparison"]["failed_count"] == 4
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
    assert result["paired_comparison"]["failed_count"] == 4
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
    panel, basis, destination, interval
):
    p = controlled_panel(panel, "trend", basis)
    trend = p.control_config["trend"]
    trend["destination"] = destination
    trend["asset_rules"]["2"] = {"indicator": "sma", "lookback_months": 8}
    # The hedge overlaps a managed asset and also contains an external instrument.
    p.control_config["hedge_weights"] = {"2": 0.4, "hedge": 0.6}
    splits = bootstrap_folds(len(p.values), 4, "sample")
    samples = [
        np.concatenate([v for _, v in bootstrap_generation(splits, 12, 42, i)]) for i in range(3)
    ]
    retained = []
    fee_rate = 0.002
    result = analyze_bootstrap(
        p,
        bootstrap_iterations=len(samples),
        objectives=["gmv"],
        user_weights=USER,
        include_paired_comparison=False,
        transaction_fee_rate=fee_rate,
        capital_gains_tax_rate=0.10,
        rebalancing_interval_months=interval,
        _sample_indices=retained,
    )
    assert "trend_sensitivity" not in result
    assert "portfolio_controls" not in result
    np.testing.assert_array_equal(retained, samples)
    sweep = analyze_bootstrap_trend_sensitivity(p, user_weights=USER, _sample_indices=retained)[
        "trend_sensitivity"
    ]
    assert sweep["validation_sample_fingerprint"] == result["validation_sample_fingerprint"]
    assert sweep["iterations"] == len(samples)
    assert set(sweep["by_asset"]) == {"1", "2"}
    for asset, series in [(None, sweep["portfolio"]), *sweep["by_asset"].items()]:
        assert np.shape(series) == (36, len(samples), 13)
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
                controlled = compare_controls(
                    varied, weights, indices, record_history=False, synthetic=True
                )["controlled"]
                expected.append(
                    [
                        controlled[side][k]
                        for side in ("net", "gross")
                        for k in ("annualized_cagr", "maximum_drawdown", "annualized_volatility")
                    ]
                    + [controlled[key] for key in sweep["metric_order"][6:]]
                )
            np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-12)
