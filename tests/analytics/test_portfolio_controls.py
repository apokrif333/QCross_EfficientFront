import numpy as np
import pytest

from app.analytics.portfolio_controls import DownsideTarget
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


@pytest.mark.parametrize("basis", ["nominal", "real"])
def test_control_api_alignment_and_original_universe(panel, settings, session_factory, basis):
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
                mode="downside_vol",
                hedge_weights={"3": 0.5, "BIL_REFERENCE": 0.5},
                downside_vol=dict(target_annual=0.09, lookback_months=3),
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
            assert result["portfolio_controls"]["assets"] == ["1", "2", "3", "BIL_REFERENCE"]
            if kind == "bootstrap":
                assert len(result["portfolio_control_samples"]) == 4
            if kind == "cross-validation":
                assert (
                    len(result["folds"][0]["portfolio_controls"]["controlled"]["net_returns"])
                    == result["folds"][0]["validation"]["observations"]
                )
