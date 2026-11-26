import numpy as np
import pytest

from app.analytics.portfolio_simulator import (
    CalendarPolicy,
    ThresholdPolicy,
    execute_target,
    simulate_portfolio,
    simulate_target_paths,
)


def test_capital_gains_tax_nets_sales_and_commissions_in_one_event():
    values = np.array([[0.6, -0.2, 0, 0, 0], [0, 0, 0, 0, 0]])
    target = [0, 0, 1, 0, 0]
    options = {
        "target_schedule": {1: target},
        "transaction_fee_rate": 0.01,
        "rebalancing_interval_months": 1,
    }
    untaxed = simulate_portfolio(values, [0.2] * 5, **options)
    path = simulate_portfolio(values, [0.2] * 5, capital_gains_tax_rate=0.10, **options)
    assert len(path["events"]) == 2
    event = path["events"][1]
    stake = 0.20 / 1.01
    assert event["trades"][:2] == pytest.approx([-1.6 * stake, -0.8 * stake])
    assert event["taxable_capital_gain"] == pytest.approx(0.4 * stake - event["commissions"])
    assert event["capital_gains_tax"] == pytest.approx(0.10 * event["taxable_capital_gain"])
    assert path["total_capital_gains_tax"] == event["capital_gains_tax"]
    assert path["net_wealth"][1] + event["commissions"] + event["capital_gains_tax"] == (
        pytest.approx(5.4 * stake)
    )
    assert path["net_wealth"][-1] < untaxed["net_wealth"][-1]
    assert path["gross_wealth"] == untaxed["gross_wealth"]
    assert (
        simulate_portfolio(-values, [0.2] * 5, capital_gains_tax_rate=0.10, **options)[
            "total_capital_gains_tax"
        ]
        == 0
    )
    assert (
        simulate_portfolio(
            values, [0.2] * 5, capital_gains_tax_rate=0.10, rebalancing_interval_months=None
        )["total_capital_gains_tax"]
        == 0
    )


def test_average_cost_and_nominal_tax_in_vectorized_real_paths():
    # Sell part of asset A, then add to the holding, then sell at the blended basis.
    values = np.array([[0.5, 0], [-1 / 3, 0], [0.5, 0], [0, 0]])
    settings = {"capital_gains_tax_rate": 0.10, "rebalancing_interval_months": 1}
    weights = [0.5, 0.5]
    nominal = simulate_portfolio(values, weights, **settings)
    first, second, third = nominal["events"][1:]
    assert first["capital_gains_tax"] == pytest.approx(0.10 / 3 * -first["trades"][0])
    assert second["capital_gains_tax"] == 0
    basis_added = second["trades"][0]
    basis_retained = nominal["positions"][1][0] * (2 / 3)
    third_position = nominal["positions"][2][0] * 1.5
    third_gain_fraction = 1 - (basis_retained + basis_added) / third_position
    assert third["capital_gains_tax"] == pytest.approx(
        0.10 * third_gain_fraction * -third["trades"][0]
    )

    inflation = np.array([1.1, 1.05, 1.01, 1.02])
    real_returns = (1 + values) / inflation[:, None] - 1
    real = simulate_portfolio(real_returns, weights, inflation_factors=inflation, **settings)
    assert real["net_wealth"][1:] == pytest.approx(
        np.asarray(nominal["net_wealth"][1:]) / inflation.cumprod()
    )
    np.testing.assert_allclose(
        [e["capital_gains_tax"] for e in real["events"][1:]],
        [
            e["capital_gains_tax"] / inflation.cumprod()[e["month_index"]]
            for e in nominal["events"][1:]
        ],
    )
    paths = np.stack([values, real_returns], axis=1)[..., None, :]
    targets = np.broadcast_to(weights, paths.shape)
    factors = np.stack([np.ones_like(inflation), inflation], axis=1)[..., None, None]
    combined = simulate_target_paths(paths, weights, targets, inflation_factors=factors, **settings)
    assert combined[0][:, 0] == pytest.approx(
        [nominal["net"]["annualized_cagr"], real["net"]["annualized_cagr"]]
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
    positive = simulate_portfolio(np.full((2, 2), 0.05), [0.5, 0.5], transaction_fee_rate=0.01)
    assert positive["net"]["terminal_wealth"] > 1
    assert positive["net"]["maximum_drawdown"] == pytest.approx(0.01 / 1.01)


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
    cv = analyze_cross_validation(
        panel, objective="max_return_at_user_risk", transaction_fee_rate=0.005, **options
    )
    record = cv["folds"][0]["paired_comparison"]["user"]
    assert record["gross_metrics"]["terminal_wealth"] > record["terminal_wealth"]
    for fold in cv["folds"]:
        optimized = fold["paired_comparison"]["optimized"]
        reported = fold["validation_metrics"]
        assert reported["historical_cagr"] == pytest.approx(optimized["annualized_cagr"])
        assert reported["historical_max_drawdown"] == pytest.approx(optimized["maximum_drawdown"])
