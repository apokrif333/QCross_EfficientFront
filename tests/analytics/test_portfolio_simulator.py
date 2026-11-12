import numpy as np
import pytest

from app.analytics.portfolio_simulator import (
    CalendarPolicy,
    ThresholdPolicy,
    execute_target,
    simulate_portfolio,
)


def test_drift_and_known_wealth_paths():
    values = np.tile([[1.0, 0.0]], (13, 1))
    monthly = simulate_portfolio(values, [0.5, 0.5], policy=CalendarPolicy(1))
    annual = simulate_portfolio(values, [0.5, 0.5])
    hold = simulate_portfolio(values, [0.5, 0.5], policy=CalendarPolicy(None))
    assert monthly["gross_wealth"][-1] == pytest.approx(1.5**13)
    assert annual["gross_wealth"][-1] == pytest.approx((0.5 * 2**12 + 0.5) * 1.5)
    assert hold["gross_wealth"][-1] == pytest.approx(0.5 * 2**13 + 0.5)
    assert annual["events"][1]["month_index"] == 11
    assert annual["weight_history"][10][0] > 0.99
    assert annual["weight_history"][11] == [0.5, 0.5]


def test_threshold_full_portfolio_and_no_lookahead():
    values = np.array([[0.5, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 0.0]])
    path = simulate_portfolio(values, [0.5, 0.5, 0.0], policy=ThresholdPolicy(0.1))
    assert path["events"][1]["month_index"] == 0
    assert path["weight_history"][0] == [0.5, 0.5, 0.0]
    assert path["net_wealth"][2] == pytest.approx(1.25**2)
    changed = values.copy()
    changed[-1] = [2, 2, 2]
    assert (
        simulate_portfolio(changed, [0.5, 0.5, 0.0], policy=ThresholdPolicy(0.1))["weight_history"][
            :2
        ]
        == path["weight_history"][:2]
    )


def test_self_financing_and_initial_fees():
    position, trades, fee = execute_target(np.array([0.8, 0.2]), np.array([0.5, 0.5]), 1.0, 0.01)
    assert position.sum() + fee == pytest.approx(1)
    assert fee == pytest.approx(0.01 * np.abs(trades).sum())
    assert trades[0] < 0 and trades[1] > 0
    path = simulate_portfolio(np.zeros((2, 2)), [0.5, 0.5], transaction_fee_rate=0.01)
    assert path["net"]["terminal_wealth"] == pytest.approx(1 / 1.01)
    assert path["gross"]["terminal_wealth"] == 1
    assert path["total_commissions"] == pytest.approx(0.01 / 1.01)
    assert path["net"]["maximum_drawdown"] == pytest.approx(0.01 / 1.01)
    assert path["trade_count"] == 2


def test_insolvency_and_zero_targets():
    path = simulate_portfolio(np.array([[-1.0, -1.0, 5.0], [0.1, 0.1, 0.1]]), [0.5, 0.5, 0.0])
    assert path["net_returns"] == [-1, 0]
    assert path["net"]["annualized_cagr"] == -1
    assert path["net"]["maximum_drawdown"] == 1
    assert path["net_wealth"] == [1, 0, 0]


def annual_returns(values, weights):
    positions = np.maximum(weights, 0).copy()
    positions /= positions.sum()
    out = []
    for i, row in enumerate(values):
        previous = positions.sum()
        positions *= 1 + row
        total = positions.sum()
        out.append(total / previous - 1 if previous > 0 else 0.0)
        if (i + 1) % 12 == 0:
            positions = total * np.maximum(weights, 0) / np.maximum(weights, 0).sum()
    return np.array(out)


@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
def test_simulation_fees_do_not_change_optimizer_or_sampling(panel, method):
    from app.analytics.bootstrap import analyze_bootstrap
    from app.analytics.cross_validation import analyze_cross_validation
    from app.analytics.frontier import analyze_frontier

    user = dict(zip(panel.assets, [0.5, 0.3, 0.2], strict=True))
    options = dict(covariance_method=method, user_weights=user)
    plain = analyze_frontier(panel, frontier_points=3, **options)
    paid = analyze_frontier(panel, frontier_points=3, transaction_fee_rate=0.005, **options)
    assert plain["gmv"]["weights"] == paid["gmv"]["weights"]
    assert plain["annual_covariance"] == paid["annual_covariance"]
    assert (
        paid["user_portfolio"]["metrics"]["historical_cagr"]
        < plain["user_portfolio"]["metrics"]["historical_cagr"]
    )
    expected = simulate_portfolio(panel.values, [0.5, 0.3, 0.2], transaction_fee_rate=0.005)
    assert paid["user_portfolio"]["metrics"]["historical_cagr"] == pytest.approx(
        expected["net"]["annualized_cagr"]
    )
    base = analyze_bootstrap(panel, bootstrap_iterations=4, objectives=["gmv"], **options)
    fee = analyze_bootstrap(
        panel, bootstrap_iterations=4, objectives=["gmv"], transaction_fee_rate=0.005, **options
    )
    assert base["sample_fingerprint"] == fee["sample_fingerprint"]
    assert (
        base["objectives"]["gmv"]["original"]["weights"]
        == fee["objectives"]["gmv"]["original"]["weights"]
    )
    cv = analyze_cross_validation(panel, transaction_fee_rate=0.005, **options)
    record = cv["folds"][0]["paired_comparison"]["user"]
    assert record["gross_metrics"]["terminal_wealth"] > record["terminal_wealth"]
