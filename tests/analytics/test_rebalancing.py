import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.analytics.models import AnalyticsError
from app.analytics.portfolio_simulator import CalendarPolicy, ThresholdPolicy, simulate_portfolio
from app.analytics.rebalancing import CALENDARS, THRESHOLDS, analyze_rebalancing
from app.ingestion.lazyportfolio.macro_service import refresh
from app.main import create_app
from tests.analytics.test_api import wait_result
from tests.analytics.test_macro_storage import seed_macro


@pytest.mark.parametrize("interval", [i for i, _ in CALENDARS])
def test_all_calendar_intervals_start_from_first_observation(interval):
    values = np.tile([0.02, -0.005], (121, 1))
    path = simulate_portfolio(values, [0.6, 0.4], policy=CalendarPolicy(interval))
    expected = list(range(interval - 1, 120, interval)) if interval else []
    assert [e["month_index"] for e in path["events"][1:]] == expected


@pytest.mark.parametrize("threshold", THRESHOLDS)
def test_all_thresholds_trigger_full_portfolio_after_month(threshold):
    values = np.array([[9.0, 0.0, -1.0], [0.0, 0.0, 5.0]])
    path = simulate_portfolio(values, [0.1, 0.9, 0], policy=ThresholdPolicy(threshold / 100))
    assert path["events"][1]["month_index"] == 0
    assert 0 in path["events"][1]["triggering_assets"]
    assert 2 not in path["events"][1]["triggering_assets"]
    assert path["weight_history"][0] == pytest.approx([0.1, 0.9, 0])
    assert path["events"][1]["trades"][0] < 0 < path["events"][1]["trades"][1]


def test_twenty_strategies_and_cost_conservation(panel):
    result = analyze_rebalancing(
        panel, user_weights={"1": 0.6, "2": 0.3, "3": 0.1}, transaction_fee_rate=0.002
    )
    assert len(result["strategies"]) == 21
    assert sum(s["annual_baseline"] for s in result["strategies"]) == 1
    assert (
        sorted(
            s["threshold_percent"]
            for s in result["strategies"]
            if s["threshold_percent"] is not None
        )
        == THRESHOLDS
    )
    for strategy in result["strategies"]:
        assert len(strategy["net_wealth"]) == 241
        assert len(strategy["weight_history"]) == 240
        assert strategy["net"]["terminal_wealth"] <= strategy["gross"]["terminal_wealth"]
        assert strategy["total_commissions"] == pytest.approx(
            sum(e["commissions"] for e in strategy["events"])
        )
        for e in strategy["events"]:
            assert e["commissions"] == pytest.approx(
                0.002 * sum(abs(t) for t in e["trades"].values())
            )
        costs = {e["month_index"]: e["commissions"] for e in strategy["events"]}
        assert sum(strategy["positions"][0]) + costs[-1] == pytest.approx(1)
        assert strategy["events"][0]["turnover"] == pytest.approx(sum(strategy["positions"][0]))
        for month, row in enumerate(panel.values):
            before = np.array(strategy["positions"][month]) * (1 + row)
            after = np.array(strategy["positions"][month + 1])
            assert after.sum() + costs.get(month, 0) == pytest.approx(before.sum(), abs=1e-12)
            assert (after >= 0).all()
    assert [s["net"]["annualized_cagr"] for s in result["strategies"]] == sorted(
        [s["net"]["annualized_cagr"] for s in result["strategies"]], reverse=True
    )


def test_historical_metrics_sortino_and_aligned_bil():
    from tests.analytics.test_portfolio_simulator import annual_returns

    values = np.array([[0.1, 0.02], [-0.1, -0.03], [0.06, 0.01], [-0.02, 0.01]])
    rf = np.array([0.001, 0.002, 0.0015, 0.001])
    monthly = annual_returns(values, np.array([0.6, 0.4]))
    wealth = np.r_[1.0, np.cumprod(1 + monthly)]
    difference = monthly - rf
    stats = simulate_portfolio(values, [0.6, 0.4], rf)["net"]
    assert stats["annualized_cagr"] == pytest.approx(wealth[-1] ** 3 - 1)
    assert stats["annualized_arithmetic_return"] == pytest.approx(monthly.mean() * 12)
    assert stats["annualized_volatility"] == pytest.approx(monthly.std(ddof=1) * np.sqrt(12))
    assert stats["historical_sharpe_ratio"] == pytest.approx(
        np.sqrt(12) * difference.mean() / difference.std(ddof=1)
    )
    assert stats["sortino_ratio"] == pytest.approx(
        np.sqrt(12) * difference.mean() / np.sqrt(np.mean(np.minimum(difference, 0) ** 2))
    )
    assert stats["maximum_drawdown"] == pytest.approx(
        (1 - wealth / np.maximum.accumulate(wealth)).max()
    )


def test_threshold_equality_and_no_trades_for_unchanged_weights():
    gain = (0.22 * 0.8) / (0.2 * 0.78) - 1
    path = simulate_portfolio(
        np.array([[gain, 0.0], [0.0, 0.0]]), [0.2, 0.8], policy=ThresholdPolicy(0.1)
    )
    assert path["events"][1]["triggering_assets"] == [0]
    assert path["pretrade_weight_history"][0][0] == pytest.approx(0.22)
    unchanged = simulate_portfolio(
        np.zeros((25, 2)), [0.2, 0.8], transaction_fee_rate=0.01, policy=CalendarPolicy(1)
    )
    assert unchanged["rebalancing_events"] == 0
    assert unchanged["trade_count"] == 2


def test_missing_bil_is_not_replaced_by_a_fixed_rate(panel):
    from dataclasses import replace

    with pytest.raises(AnalyticsError, match="BIL"):
        analyze_rebalancing(
            replace(panel, risk_free_source="historical_bil"),
            user_weights={"1": 0.6, "2": 0.3, "3": 0.1},
        )


@pytest.mark.parametrize("basis", ["nominal", "real"])
def test_async_rebalancing_job_ignores_global_objective_and_cv_history(
    panel, settings, session_factory, basis
):
    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as session:
        refresh(session, rows, nominal, snapshot)
        session.commit()
    with TestClient(create_app(settings)) as client:
        body = dict(
            instrument_ids=[1, 2, 3],
            user_weights={"1": 0.6, "2": 0.3, "3": 0.1},
            return_basis=basis,
            risk_free_source="historical_bil",
            transaction_fee_rate=0.002,
            objective="target_return",
            target_return=None,
            covariance_method="cv_linear",
            cv_folds=20,
        )
        submitted = client.post("/api/v1/analytics/rebalancing-optimization", json=body)
        assert submitted.status_code == 202, submitted.text
        done = wait_result(client, submitted.json())
        assert done["status"] == "completed", done
        result = done["result"]
        assert len(result["strategies"]) == 21
        assert result["return_basis"] == basis
        assert result["reproducibility"]["api_request"]["transaction_fee_rate"] == 0.002
        invalid = {**body, "user_weights": None}
        assert (
            client.post("/api/v1/analytics/rebalancing-optimization", json=invalid).status_code
            == 422
        )
        invalid = {**body, "transaction_fee_rate": -1}
        assert (
            client.post("/api/v1/analytics/rebalancing-optimization", json=invalid).status_code
            == 422
        )
