import numpy as np
import pytest
from pydantic import ValidationError

from app.analytics.constraints import numbered_groups, user_allocation
from app.analytics.frontier import analyze_frontier
from app.analytics.models import AnalyticsError
from app.schemas.frontier import AnalyticsRequest


def test_user_portfolio_uses_compounding_and_population_historical_volatility(panel):
    result = analyze_frontier(panel, user_weights={"1": 1}, frontier_points=3)
    user = result["user_portfolio"]
    values = panel.values[:, 0]
    assert user["weights"] == {"1": 1, "2": 0, "3": 0}
    assert user["feasible"]
    assert user["metrics"]["expected_return"] == pytest.approx(values.mean() * 12)
    assert user["metrics"]["historical_cagr"] == pytest.approx(
        np.prod(1 + values) ** (12 / len(values)) - 1
    )
    assert user["metrics"]["historical_volatility"] == pytest.approx(
        values.std(ddof=0) * np.sqrt(12)
    )
    assert user["metrics"]["volatility"] == pytest.approx(values.std(ddof=1) * np.sqrt(12))
    assert result["asset_portfolios"]["1"]["metrics"] == user["metrics"]
    for column, asset in enumerate(panel.assets):
        reference = result["asset_portfolios"][asset]
        assert reference["weights"][asset] == 1
        assert sum(reference["weights"].values()) == 1
        observations = panel.values[:, column]
        assert reference["metrics"]["historical_cagr"] == pytest.approx(
            np.prod(1 + observations) ** (12 / len(observations)) - 1
        )
        assert reference["metrics"]["historical_volatility"] == pytest.approx(
            observations.std(ddof=0) * np.sqrt(12)
        )


def test_numbered_groups_and_infeasible_reference(panel):
    grouped = numbered_groups(panel, {"1": 1, "2": 3, "3": 5})
    result = analyze_frontier(
        grouped,
        user_weights={"1": 1},
        frontier_points=3,
        group_constraints={"Group 1": {"max": 0.4}, "Group 3": {"min": 0.2}},
    )
    assert panel.groups == {"Equity": ["1", "3"], "US Stocks": ["1"]}
    assert result["allocation_groups"]["Group 5"] == ["3"]
    assert not result["user_portfolio"]["feasible"]
    assert result["user_portfolio"]["diagnostics"]["constraint_violation"] == pytest.approx(0.6)
    for name in ["gmv", "max_sharpe"]:
        assert result[name]["weights"]["1"] <= 0.4 + 1e-7
        assert result[name]["weights"]["2"] >= 0.2 - 1e-7


@pytest.mark.parametrize("weights", [{}, None, {"1": 0, "2": 0}])
def test_zero_allocation_omits_reference(weights):
    assert user_allocation(["1", "2"], weights) is None


@pytest.mark.parametrize(
    "weights", [{"1": 0.4}, {"1": -0.1, "2": 1.1}, {"4": 1}, {"1": float("nan")}]
)
def test_invalid_allocation_rejected(weights):
    with pytest.raises(AnalyticsError):
        user_allocation(["1", "2"], weights)


@pytest.mark.parametrize(
    "override",
    [
        {"asset_groups": {"1": 6}},
        {"asset_groups": {"1": True}},
        {"asset_groups": {"3": 1}},
        {"user_weights": {"1": 0.5}},
    ],
)
def test_request_rejects_invalid_user_inputs(override):
    with pytest.raises(ValidationError):
        AnalyticsRequest(instrument_ids=[1, 2], **override)
