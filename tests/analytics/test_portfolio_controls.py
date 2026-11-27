import numpy as np
import pytest

from app.analytics.portfolio_controls import DownsideTarget, TrendTarget
from app.analytics.portfolio_simulator import simulate_portfolio
from app.schemas.portfolio_controls import PortfolioControls


def test_downside_exact_warmup_and_hedge_independence():
    values = np.array([[-0.1, 0.5]] * 4 + [[0.1, 0.5]])

    def run(hedge_returns):
        data = values.copy()
        data[:, 1] = hedge_returns
        provider = DownsideTarget(np.array([1.0, 0]), np.array([0.0, 1]), 0.09, 3)
        return simulate_portfolio(data, [1, 0], target_provider=provider)

    first, second = run(0.5), run(-0.5)
    assert first["signal_history"][1]["alpha"] == 1
    assert first["signal_history"][2]["raw_alpha"] == pytest.approx(0.09 / (np.sqrt(12) * 0.1))
    assert first["signal_history"][2]["alpha"] == 0.3
    assert first["signal_history"] == second["signal_history"]
    assert first["events"][1]["month_index"] == 2
    assert first["weight_history"][2] == pytest.approx([0.3, 0.7])


def test_single_month_downside_annualization_and_validation():
    config = PortfolioControls(
        mode="downside_vol",
        hedge_weights={"2": 1},
        downside_vol={"target_annual": 0.09, "lookback_months": 1},
    )
    assert config.downside_vol.lookback_months == 1
    values = np.array([[-0.1, 0], [0.2, 0]])
    provider = DownsideTarget(np.array([1.0, 0]), np.array([0.0, 1]), 0.09, 1)
    result = simulate_portfolio(values, [1, 0], target_provider=provider)
    assert result["signal_history"][0]["downside_volatility"] == pytest.approx(np.sqrt(12) * 0.1)
    assert result["signal_history"][0]["raw_alpha"] == pytest.approx(0.09 / (np.sqrt(12) * 0.1))
    assert result["signal_history"][0]["alpha"] == 0.3
    expected = np.sqrt(12 * np.mean(np.minimum(result["net_returns"], 0) ** 2))
    assert result["net"]["annualized_downside_volatility"] == pytest.approx(expected)


def test_downside_trades_when_rounded_target_changes():
    raw_targets = np.array(
        [
            0.95,
            0.949,
            0.93,
            0.85,
            0.849,
            0.75,
            0.749,
            0.35,
            0.33,
            0.27,
            0.249,
            0.25,
            0.049,
            0.05,
            0.12,
            1,
        ]
    )
    expected_targets = [1, 0.9, 0.9, 0.9, 0.8, 0.8, 0.7, 0.4, 0.3, 0.3, 0.2, 0.3, 0, 0.1, 0.1, 1]
    base_returns = -0.05 / (np.sqrt(12) * raw_targets)
    values = np.column_stack([base_returns, np.zeros_like(base_returns)])
    provider = DownsideTarget(np.array([1.0, 0]), np.array([0.0, 1]), 0.05, 1)
    path = simulate_portfolio(
        values, [1, 0], target_provider=provider, rebalancing_interval_months=None
    )

    signals = path["signal_history"]
    assert [signal["raw_alpha"] for signal in signals] == pytest.approx(raw_targets)
    assert [signal["alpha"] for signal in signals] == expected_targets
    events = path["events"][1:]
    assert [event["month_index"] for event in events] == [1, 4, 6, 7, 8, 10, 11, 12, 13]
    assert all(event["reason"] == "control_change" for event in events)
    assert path["weight_history"][12] == [0, 1]
    assert path["weight_history"][13] == pytest.approx([0.1, 0.9])


def test_no_monthly_rebalance_for_unchanged_signal_and_no_lookahead():
    values = np.full((25, 2), 0.01)
    values[:, 1] = 0.02

    def run(v):
        return simulate_portfolio(
            v,
            [0.6, 0.4],
            target_provider=DownsideTarget(np.array([0.6, 0.4]), np.array([0, 1]), 0.09, 3),
        )

    base = run(values)
    assert base["rebalancing_events"] == 2
    changed = values.copy()
    changed[-1] = [-0.9, 0]
    assert run(changed)["weight_history"][:-1] == base["weight_history"][:-1]
    assert base["signal_history"][3]["alpha"] == 1


def test_control_schema_requires_hedge():
    with pytest.raises(ValueError):
        PortfolioControls(
            mode="downside_vol", downside_vol={"target_annual": 0.09, "lookback_months": 12}
        )


@pytest.mark.parametrize("indicator", ["sma", "momentum"])
def test_trend_exit_reentry_and_month_boundary(indicator):
    values = np.array([[-0.1, 0], [-0.1, 0], [0.3, 0], [0.2, 0]])
    provider = TrendTarget(
        np.array([1.0, 0]),
        np.array([0.0, 1]),
        ["asset", "hedge"],
        {"asset": {"indicator": indicator, "lookback_months": 2}},
        "hedge",
    )
    path = simulate_portfolio(values, [1, 0], target_provider=provider)
    assert path["weight_history"][0] == [1, 0]
    assert path["weight_history"][1] == [0, 1]
    assert path["weight_history"][2] == [1, 0]
    assert path["net_returns"][2] == 0  # recovery was observed, not earned before reentry
    assert path["events"][1]["control_state"]["exits"] == ["asset"]
    assert path["events"][2]["control_state"]["entries"] == ["asset"]


@pytest.mark.parametrize("indicator", ["momentum", "sma", "downside_vol"])
def test_real_reporting_preserves_nominal_signals(panel, indicator):
    from dataclasses import replace

    from app.analytics.control_loader import transform_control_panel
    from app.analytics.portfolio_controls import (
        analyze_asset_trend_sensitivity,
        analyze_bootstrap_trend_sensitivity,
        attach_control_results,
        compare_controls,
    )

    months = panel.returns.index
    inflation = 0.02 + 0.002 * (np.arange(len(months)) % 2)
    frame = panel.returns.assign(**{"1": 0.01, "2": 0.0})
    trend = indicator != "downside_vol"
    weights = {"1": 1.0}
    settings = {"transaction_fee_rate": 0.001, "rebalancing_interval_months": 12}
    nominal = replace(
        panel,
        returns=frame,
        control_returns=frame,
        control_config={
            "mode": "trend" if trend else "downside_vol",
            "hedge_weights": {"2": 1},
            "downside_vol": {"target_annual": 0.005, "lookback_months": 3},
            "trend": {
                "destination": "hedge",
                "asset_rules": {"1": {"indicator": indicator, "lookback_months": 3}},
            },
        },
        simulation_options=settings,
    )
    real = replace(
        nominal,
        return_basis="real",
        returns=(1 + frame).div(1 + inflation, axis=0) - 1,
        reference_metadata={
            "inflation_observations": [
                [str(month), float(rate), "observed"]
                for month, rate in zip(months, inflation, strict=True)
            ]
        },
    )
    transform_control_panel(real)
    result = {}
    attach_control_results(real, result, weights)
    controlled = result["portfolio_controls"]
    nominal_path = compare_controls(nominal, weights)["controlled"]
    real_path = controlled["controlled"]
    expected_monthly = (1 + np.asarray(nominal_path["net_returns"])) / (1 + inflation) - 1
    assert controlled["signal_basis"] == "nominal"
    assert nominal_path["signal_history"] == real_path["signal_history"]
    assert nominal_path["rebalancing_events"] == real_path["rebalancing_events"] == 0
    np.testing.assert_allclose(real_path["net_returns"], expected_monthly, atol=1e-14)
    assert real_path["net"]["annualized_cagr"] < 0
    assert nominal_path["net"]["annualized_cagr"] > 0

    if trend:
        period = 6
        expected = real_path["net"]["annualized_cagr"]
        assert controlled["sensitivity"]["points"][period - 1]["net"]["annualized_cagr"] == (
            pytest.approx(expected)
        )
        asset = analyze_asset_trend_sensitivity(real, user_weights=weights, **settings)["by_asset"]
        assert asset["1"]["points"][period - 1]["net"] == pytest.approx(
            {
                key: real_path["net"][key]
                for key in (
                    "annualized_cagr",
                    "annualized_volatility",
                    "maximum_drawdown",
                    "historical_sharpe_ratio",
                    "sortino_ratio",
                    "calmar_ratio",
                )
            }
        )
        sampled = np.roll(np.arange(len(months)), 1)
        sweep = analyze_bootstrap_trend_sensitivity(
            real, user_weights=weights, _sample_indices=[sampled]
        )["trend_sensitivity"]
        assert sweep["portfolio"][period - 1][0][0] == pytest.approx(expected)
        assert sweep["by_asset"]["1"][period - 1][0][0] == pytest.approx(expected)


@pytest.mark.parametrize(
    "destination,expected", [("remaining", [0, 1, 0]), ("hedge", [0.3, 0.4, 0.3])]
)
def test_trend_destinations_overlap_and_fees(destination, expected):
    values = np.array([[-0.1, 0.01, 0], [-0.1, 0.01, 0], [0, 0, 0]])
    provider = TrendTarget(
        np.array([0.6, 0.4, 0]),
        np.array([0.5, 0, 0.5]),
        ["a", "b", "h"],
        {"a": {"indicator": "momentum", "lookback_months": 2}},
        destination,
    )
    path = simulate_portfolio(
        values, [0.6, 0.4, 0], target_provider=provider, transaction_fee_rate=0.001
    )
    assert path["weight_history"][1] == pytest.approx(expected)
    assert len(path["events"]) == 2
    event = path["events"][1]
    assert event["commissions"] == pytest.approx(0.001 * np.abs(event["trades"]).sum())


def test_all_inactive_fallback_and_disabled_asset():
    data = np.full((4, 3), -0.1)
    rules = {a: {"indicator": "momentum", "lookback_months": 2} for a in ["a", "b"]}
    path = simulate_portfolio(
        data,
        [0.6, 0.4, 0],
        target_provider=TrendTarget(
            np.array([0.6, 0.4, 0]), np.array([0, 0, 1]), ["a", "b", "h"], rules, "remaining"
        ),
    )
    assert path["weight_history"][1] == [0, 0, 1]
    rules["b"]["indicator"] = "disabled"
    path = simulate_portfolio(
        data,
        [0.6, 0.4, 0],
        target_provider=TrendTarget(
            np.array([0.6, 0.4, 0]), np.array([0, 0, 1]), ["a", "b", "h"], rules, "remaining"
        ),
    )
    assert path["weight_history"][1] == [0, 1, 0]


def test_controls_preserve_optimizer_and_bootstrap_indices(panel):
    from dataclasses import replace

    from app.analytics.bootstrap import analyze_bootstrap

    config = {
        "mode": "trend",
        "hedge_weights": {"2": 1},
        "trend": {
            "destination": "hedge",
            "asset_rules": {"1": {"indicator": "momentum", "lookback_months": 3}},
        },
    }
    options = dict(bootstrap_iterations=4, user_weights={"1": 0.6, "2": 0.4}, objectives=["gmv"])
    plain = analyze_bootstrap(panel, **options)
    controlled = analyze_bootstrap(
        replace(panel, control_config=config, control_returns=panel.returns), **options
    )
    assert plain["sample_fingerprint"] == controlled["sample_fingerprint"]

    def without_timing(value):
        if isinstance(value, dict):
            return {
                k: without_timing(v)
                for k, v in value.items()
                if k not in {"solve_seconds", "wall_seconds"}
            }
        if isinstance(value, list):
            return [without_timing(v) for v in value]
        return value

    assert without_timing(plain["objectives"]) == without_timing(controlled["objectives"])
    passive_pairs = plain["paired_comparison"]
    managed_pairs = controlled["paired_comparison"]
    assert passive_pairs["user_portfolio_mode"] == "passive"
    assert managed_pairs["user_portfolio_mode"] == "controlled"
    assert passive_pairs["generated_datasets"] == managed_pairs["generated_datasets"] == 4
    # Validation reference moments still use the same generated market observations.
    assert passive_pairs["annual_mean"] == managed_pairs["annual_mean"]
    assert passive_pairs["annual_covariance"] == managed_pairs["annual_covariance"]
    for i, pair in enumerate(managed_pairs["results"]):
        sample = controlled["portfolio_control_samples"][i]
        assert pair["user"]["annualized_cagr"] == sample["controlled"]["net"]["annualized_cagr"]
        assert (
            passive_pairs["results"][i]["user"]["annualized_cagr"]
            == sample["baseline"]["net"]["annualized_cagr"]
        )


@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("control_mode", ["downside_vol", "trend"])
def test_control_api_alignment_and_original_universe(
    panel, settings, session_factory, basis, control_mode
):
    from fastapi.testclient import TestClient

    from app.ingestion.lazyportfolio.macro_service import refresh
    from app.main import create_app
    from tests.analytics.test_api import wait_result
    from tests.analytics.test_macro_storage import seed_macro

    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as session:
        refresh(session, rows, nominal, snapshot)
        session.commit()
    with TestClient(create_app(settings)) as client:
        body = dict(
            instrument_ids=[1, 2],
            user_weights={"1": 0.6, "2": 0.4},
            return_basis=basis,
            portfolio_controls=dict(
                mode=control_mode,
                hedge_weights={"3": 0.5, "BIL_REFERENCE": 0.5},
                downside_vol=dict(target_annual=0.09, lookback_months=3),
                trend=dict(
                    destination="hedge",
                    asset_rules={"1": dict(indicator="sma", lookback_months=3)},
                ),
            ),
            frontier_points=3,
            bootstrap_iterations=4,
        )
        preview = client.post("/api/v1/analytics/data-preview", json=body)
        assert preview.status_code == 200, preview.text
        for kind in ["frontier", "cross-validation", "bootstrap"]:
            job = client.post(f"/api/v1/analytics/{kind}", json=body)
            assert job.status_code == 202, job.text
            done = wait_result(client, job.json())
            assert done["status"] == "completed", done
            result = done["result"]
            assert result["assets"] == ["1", "2"]
            assert result["reference_metadata"]["portfolio_controls"]["signal_basis"] == "nominal"
            if control_mode == "trend" and kind == "bootstrap":
                assert done["bootstrap_trend_sensitivity_available"]
                assert "portfolio_controls" not in result
            elif control_mode == "trend":
                assert [
                    p["lookback_months"]
                    for p in result["portfolio_controls"]["sensitivity"]["points"]
                ] == list(range(1, 37))
            if kind != "bootstrap":
                assert result["portfolio_controls"]["assets"] == ["1", "2", "3", "99"]
            if kind == "bootstrap":
                assert len(result["portfolio_control_samples"]) == 4
            if kind == "cross-validation":
                assert result["paired_comparison"]["user_portfolio_mode"] == "controlled"
                assert (
                    len(result["folds"][0]["portfolio_controls"]["controlled"]["net_returns"])
                    == result["folds"][0]["validation"]["observations"]
                )
        overlap = {
            **body,
            "instrument_ids": [1, 99],
            "user_weights": {"1": 0.6, "99": 0.4},
            "portfolio_controls": {
                **body["portfolio_controls"],
                "hedge_weights": {"99": 0.5, "BIL_REFERENCE": 0.5},
            },
        }
        response = client.post("/api/v1/analytics/frontier", json=overlap)
        assert response.status_code == 202, response.text
        done = wait_result(client, response.json())
        assert done["status"] == "completed", done
        assert done["result"]["portfolio_controls"]["assets"] == ["1", "99"]
        assert done["result"]["portfolio_controls"]["config"]["hedge_weights"] == {"99": 1.0}


@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
@pytest.mark.parametrize("basis", ["nominal", "real"])
def test_all_estimators_leave_model_moments_unchanged(panel, method, basis):
    from dataclasses import replace

    from app.analytics.frontier import analyze_frontier

    frame = panel.returns.copy()
    if basis == "real":
        frame = (1 + frame) / 1.002 - 1
    base = replace(panel, returns=frame, return_basis=basis)
    config = {
        "mode": "trend",
        "hedge_weights": {"2": 1},
        "trend": {
            "destination": "remaining",
            "asset_rules": {"1": {"indicator": "sma", "lookback_months": 12}},
        },
    }
    options = dict(user_weights={"1": 0.6, "2": 0.4}, covariance_method=method, frontier_points=3)
    original = analyze_frontier(base, **options)
    controlled = analyze_frontier(
        replace(
            base,
            control_config=config,
            control_returns=frame,
            control_signal_returns=panel.returns if basis == "real" else None,
        ),
        **options,
    )
    assert original["annual_covariance"] == controlled["annual_covariance"]
    assert original["gmv"]["weights"] == controlled["gmv"]["weights"]
    assert (
        original["reproducibility"]["calculation_sha256"]
        != controlled["reproducibility"]["calculation_sha256"]
    )
    assert controlled["portfolio_controls"]["baseline"]["net"]["annualized_cagr"] == pytest.approx(
        original["user_portfolio"]["metrics"]["historical_cagr"]
    )


def test_hedge_shortening_and_fingerprint(panel, monkeypatch):
    from dataclasses import replace

    from app.analytics.control_loader import load_control_panel, transform_control_panel
    from app.analytics.provenance import panel_provenance

    original = replace(panel, returns=panel.returns[["1", "2"]], assets=["1", "2"])
    monkeypatch.setattr(
        "app.analytics.control_loader.load_returns",
        lambda *a, **kw: replace(panel, returns=panel.returns.iloc[60:]),
    )
    cfg = {
        "mode": "downside_vol",
        "hedge_weights": {"3": 1},
        "downside_vol": {"target_annual": 0.09, "lookback_months": 3},
    }
    aligned = load_control_panel(None, original, cfg)
    assert len(aligned.values) == 180
    assert aligned.assets == ["1", "2"]
    assert any("shortened" in w for w in aligned.warnings)
    transform_control_panel(aligned)
    first = panel_provenance(aligned)["reference_panel_sha256"]
    aligned.control_returns.iloc[0, 2] += 0.001
    transform_control_panel(aligned)
    assert panel_provenance(aligned)["reference_panel_sha256"] != first


def test_zero_weight_sensitivity_does_not_run_extra_simulations(panel, monkeypatch):
    from dataclasses import replace

    from app.analytics.portfolio_controls import attach_control_results

    config = {
        "mode": "trend",
        "hedge_weights": {"2": 1},
        "trend": {
            "destination": "hedge",
            "asset_rules": {"3": {"indicator": "sma", "lookback_months": 12}},
            "sensitivity_asset": "3",
        },
    }
    panel = replace(panel, control_config=config, control_returns=panel.returns)
    calls = []

    def fake_compare(*args, **kwargs):
        calls.append(kwargs)
        return {"controlled": {}}

    monkeypatch.setattr("app.analytics.portfolio_controls.compare_controls", fake_compare)
    result = {}
    attach_control_results(panel, result, {"1": 0.6, "2": 0.4, "3": 0})
    assert len(calls) == 1
    assert "sensitivity" not in result["portfolio_controls"]


def test_sensitivity_applies_common_period_to_all_enabled_assets(panel):
    from copy import deepcopy
    from dataclasses import replace

    from app.analytics.portfolio_controls import attach_control_results, compare_controls
    from app.analytics.portfolio_simulator import COST_METRICS

    config = {
        "mode": "trend",
        "hedge_weights": {"3": 1},
        "trend": {
            "destination": "hedge",
            "asset_rules": {
                "1": {"indicator": "momentum", "lookback_months": 10},
                "2": {"indicator": "sma", "lookback_months": 12},
                "3": {"indicator": "disabled", "lookback_months": 8},
            },
            "sensitivity_asset": "1",
        },
    }
    panel = replace(
        panel,
        control_config=config,
        control_returns=panel.returns,
        simulation_options={"transaction_fee_rate": 0.002},
    )
    weights = {"1": 0.5, "2": 0.3, "3": 0.2}
    result = {"portfolio_controls": compare_controls(panel, weights)}
    attach_control_results(panel, result, weights)
    controls = result["portfolio_controls"]
    assert controls["sensitivity"]["scope"] == "common_lookback"
    points = controls["sensitivity"]["points"]
    assert [p["lookback_months"] for p in points] == list(range(1, 37))
    for period in (1, 10, 12, 36):
        changed = deepcopy(config)
        for asset in ("1", "2"):
            changed["trend"]["asset_rules"][asset]["lookback_months"] = period
        expected = compare_controls(replace(panel, control_config=changed), weights)["controlled"]
        assert points[period - 1] == {
            "lookback_months": period,
            "gross": expected["gross"],
            "net": expected["net"],
            "trading_costs": {key: expected[key] for key in COST_METRICS},
        }
    assert controls["config"]["trend"]["asset_rules"] == config["trend"]["asset_rules"]
