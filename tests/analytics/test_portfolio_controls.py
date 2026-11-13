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
    expected = 0.09 / (np.sqrt(12) * 0.1)
    assert first["signal_history"][2]["alpha"] == pytest.approx(expected)
    assert first["signal_history"] == second["signal_history"]
    assert first["events"][1]["month_index"] == 2
    assert first["weight_history"][2] == pytest.approx([expected, 1 - expected])


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
    assert result["signal_history"][0]["alpha"] == pytest.approx(0.09 / (np.sqrt(12) * 0.1))
    expected = np.sqrt(12 * np.mean(np.minimum(result["net_returns"], 0) ** 2))
    assert result["net"]["annualized_downside_volatility"] == pytest.approx(expected)


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
    assert without_timing(plain["paired_comparison"]) == without_timing(
        controlled["paired_comparison"]
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
                    sensitivity_asset="1",
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
            if control_mode == "trend":
                assert [
                    p["lookback_months"]
                    for p in result["portfolio_controls"]["sensitivity"]["points"]
                ] == list(range(2, 37))
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
        replace(base, control_config=config, control_returns=frame), **options
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
