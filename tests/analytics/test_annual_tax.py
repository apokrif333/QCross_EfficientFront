from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import brentq

from app.analytics.bootstrap import analyze_bootstrap, bootstrap_generation
from app.analytics.portfolio_simulator import (
    TARGET_PATH_METRICS,
    _execute_taxable_target,
    simulate_portfolio,
    simulate_target_paths,
)
from app.analytics.rebalancing import analyze_rebalancing
from app.analytics.splitting import bootstrap_folds


@pytest.mark.parametrize("terminal_month", [11, 12])
def test_annual_netting_and_terminal_close(terminal_month):
    # A $100k portfolio with gains/losses of +10k, -8k, +5k, -4k.
    returns = [[0.10, 0], [0, -0.08 / 1.10], [0.05 / 1.02, 0], [0, -0.04 / 1.07], [0.02, 0]]
    switches = {1: [0, 1], 2: [1, 0], 3: [0, 1], 4: [1, 0]}
    path = simulate_portfolio(
        returns,
        [1, 0],
        target_schedule=switches,
        capital_gains_tax_rate=0.10,
        rebalancing_interval_months=None,
        tax_months=np.arange(terminal_month - 4, terminal_month + 1),
    )
    initial, *trades, close = path["events"]
    assert initial["annual_realized_gain"] == 0
    assert [e["realized_capital_gain"] for e in trades] == pytest.approx([0.10, -0.08, 0.05, -0.04])
    assert [e["capital_gains_tax"] for e in trades] == [0] * 4
    assert close["kind"] == "terminal_tax_settlement"
    assert close["annual_realized_gain"] - close["realized_capital_gain"] == pytest.approx(0.03)
    assert close["capital_gains_tax"] > 0.003  # a gain on the tax-funding sale
    assert 0 < close["sales"] < close["pretrade_nav"]
    assert close["taxable_capital_gain"] < 0.03 + 0.02 * 1.03
    assert path["rebalancing_events"] == 4
    assert path["tax_year_close_months"] == [4]
    assert path["net_returns"][-1] < path["gross_returns"][-1]
    untaxed = simulate_portfolio(
        returns, [1, 0], target_schedule=switches, rebalancing_interval_months=None
    )
    assert path["gross_wealth"] == untaxed["gross_wealth"]
    assert path["weight_history"][-1] == pytest.approx(untaxed["weight_history"][-1])
    for e in path["events"]:
        assert e["pretrade_nav"] == pytest.approx(
            e["posttrade_nav"] + e["commissions"] + e.get("capital_gains_tax", 0)
        )


def test_loss_expires_at_december_and_unsold_cost_basis_survives():
    path = simulate_portfolio(
        [[-0.4, 0, 0.5], [0, 0, 0], [0, 0.5, 0], [0, 0, 0]],
        [0.5, 0, 0.5],
        target_schedule={1: [0, 0.3 / 1.05, 0.75 / 1.05], 3: [1, 0, 0]},
        tax_months=[11, 12, 1, 2],
        capital_gains_tax_rate=0.1,
        rebalancing_interval_months=None,
    )
    _, loss, gain, close = path["events"]
    assert path["tax_year_close_months"] == [1, 3]
    assert loss["annual_realized_gain"] == pytest.approx(-0.2)
    assert loss["cost_basis"][2] == pytest.approx(0.5)
    assert gain["realized_capital_gain"] == pytest.approx(0.15 + 0.25)
    assert gain["annual_realized_gain"] == pytest.approx(0.4)
    assert close["capital_gains_tax"] == pytest.approx(0.04)


def test_initial_entry_partial_sale_average_cost_and_full_exit_then_reentry():
    fee = 0.01
    path = simulate_portfolio(
        [[0.5, 0], [-0.3, 0], [0.5, 0], [0, 0], [0, 0], [0, 0]],
        [0.5, 0.5],
        target_schedule={4: [0, 1], 5: [0.5, 0.5]},
        transaction_fee_rate=fee,
        capital_gains_tax_rate=0.1,
        rebalancing_interval_months=1,
    )
    entry, first, second, third, exit_trade, reentry, _ = path["events"]
    assert entry["posttrade_nav"] == pytest.approx(1 / (1 + fee))
    assert entry["cost_basis"] == pytest.approx([0.5, 0.5])
    assert entry["annual_realized_gain"] == 0
    # Check proportional basis for the original stake, then a buy at a new price.
    sold = -first["trades"][0]
    allocated = 0.5 * sold / (entry["trades"][0] * 1.5)
    assert first["cost_basis"][0] == pytest.approx(0.5 - allocated)
    assert first["realized_capital_gain"] == pytest.approx(sold * (1 - fee) - allocated)
    bought = second["trades"][0]
    assert bought > 0
    blended = 0.5 - allocated + bought * (1 + fee)
    assert second["cost_basis"][0] == pytest.approx(blended)
    disposed = -third["trades"][0]
    disposed_basis = blended * disposed / (path["positions"][2][0] * 1.5)
    assert third["cost_basis"][0] == pytest.approx(blended - disposed_basis)
    assert third["realized_capital_gain"] == pytest.approx(disposed * (1 - fee) - disposed_basis)
    assert exit_trade["cost_basis"][0] == 0
    assert reentry["cost_basis"][0] == pytest.approx(reentry["trades"][0] * (1 + fee))


@pytest.mark.parametrize("basis, sign", [([0.14, 0.4], 1), ([1.2, 0.8], -1)])
def test_funding_sales_preserve_drift_and_change_final_tax_base(basis, sign):
    old = np.array([0.8, 0.4])
    before, gain, rate, fee = old.sum(), 0.25, 0.2, 0.023
    weights = old / before
    positions, trades, cost, tax, realized, _, unpaid = _execute_taxable_target(
        old, weights, before, fee, rate, np.array(basis), gain
    )
    slope = (old * (1 - fee) - basis).sum()
    fraction = rate * gain / (before * (1 - fee) - rate * slope)
    assert positions == pytest.approx(old * (1 - fraction))
    assert all(trades < 0)
    assert cost == pytest.approx(fee * fraction * before)
    assert np.sign(realized) == sign
    assert tax == pytest.approx(rate * (gain + fraction * slope))
    assert unpaid == 0


@pytest.mark.parametrize("close_month", [11, 12])
def test_fold_change_inside_tax_year_and_on_tax_boundary(close_month):
    path = simulate_portfolio(
        [[0.4, 0, 0], [0, -0.1, 0.3], [0, 0, 0]],
        [0.5, 0, 0.5],
        target_schedule={1: [0, 0.5, 0.5], 2: [0.5, 0, 0.5]},
        capital_gains_tax_rate=0.25,
        transaction_fee_rate=0.01,
        rebalancing_interval_months=2,
        tax_months=[close_month - 1, close_month, close_month % 12 + 1],
    )
    _, fold_one, fold_two, *later = path["events"]
    assert fold_one["kind"] == "fold_target_change"
    assert fold_two["reason"] == "fold_target_change"
    assert fold_two["annual_realized_gain"] == pytest.approx(
        fold_one["realized_capital_gain"] + fold_two["realized_capital_gain"]
    )
    turnover = np.abs(fold_two["trades"]).sum()
    assert fold_two["commissions"] == pytest.approx(0.01 * turnover)
    assert fold_two["pretrade_nav"] == pytest.approx(
        fold_two["posttrade_nav"] + fold_two["commissions"] + fold_two["capital_gains_tax"]
    )
    if close_month == 12:
        assert fold_two["kind"] == "combined_rebalance_and_tax_settlement"
        assert fold_two["capital_gains_tax"] > 0
        assert later == []
    else:
        assert fold_two["capital_gains_tax"] == 0
        assert later[0]["kind"] == "terminal_tax_settlement"


def test_joint_solver_matches_independent_bisection_and_conserves_capital():
    def balance(end, target, old, basis, annual_gain, wealth, fee, tax_rate):
        delta = target * end - old
        realized_gain = np.maximum(0, -delta) @ (1 - fee - basis / old)
        return (
            end
            + fee * np.abs(delta).sum()
            + tax_rate * max(0, annual_gain + realized_gain)
            - wealth
        )

    rng = np.random.default_rng(42)
    for fee, tax_rate in [(0, 0.10), (0.002, 0.10), (0.07, 1), (0.95, 0.99999)]:
        old = rng.uniform(0.002, 2, (200, 5))
        weights = rng.dirichlet(np.ones(5), len(old))
        basis = old * rng.uniform(0.01, 3, old.shape)
        gains = rng.uniform(-2, 8, len(old))
        wealth = old.sum(axis=1)
        positions, trades, fees, paid, realized, cost_basis, unpaid = _execute_taxable_target(
            old, weights, wealth[:, None], fee, tax_rate, basis, gains
        )
        np.testing.assert_allclose(wealth, positions.sum(1) + fees + paid, rtol=1e-11)
        np.testing.assert_allclose(paid + unpaid, tax_rate * np.maximum(0, gains + realized))
        assert (positions >= 0).all() and (cost_basis >= 0).all()
        for i in range(len(old)):
            args = (weights[i], old[i], basis[i], gains[i], wealth[i], fee, tax_rate)
            expected = (
                0
                if balance(0, *args) >= 0
                else brentq(balance, 0, wealth[i], args=args, xtol=1e-14)
            )
            assert positions[i].sum() == pytest.approx(expected, rel=1e-10, abs=1e-12)
        np.testing.assert_allclose(trades, positions - old)


@pytest.mark.parametrize("available_weight", [0, 0.01])
def test_insolvency_keeps_scenario_and_reports_unpaid_tax(available_weight):
    returns = np.array([[9, 0, 0], [0, -1, 0], [0, 0.5, 0]])
    calendar = [11, 12, 1]
    settings = dict(
        transaction_fee_rate=0.001,
        capital_gains_tax_rate=0.99,
        tax_months=calendar,
        rebalancing_interval_months=None,
    )
    desired = [0, 1 - available_weight, available_weight]
    path = simulate_portfolio(returns, [1, 0, 0], target_schedule={1: desired}, **settings)
    close = path["events"][-1]
    assert close["kind"] == "tax_settlement"
    assert close["capital_gains_tax"] == pytest.approx(close["pretrade_nav"] - close["commissions"])
    assert path["tax_insolvent"] and path["unpaid_capital_gains_tax"] > 0
    assert path["net"]["terminal_wealth"] == 0
    assert path["net"]["maximum_drawdown"] == 1
    assert path["net"]["annualized_cagr"] == -1
    np.testing.assert_array_equal(path["positions"][-1], 0)
    targets = np.tile(desired, (3, 1, 1))
    batched = dict(
        zip(
            TARGET_PATH_METRICS,
            simulate_target_paths(returns[:, None], [1, 0, 0], targets, **settings),
            strict=True,
        )
    )
    assert batched["unpaid_capital_gains_tax"] == pytest.approx(path["unpaid_capital_gains_tax"])


def test_real_equivalence_and_cost_normalization_with_distinct_tax_months():
    returns = np.array([[0.5, 0], [0, -0.13], [0.06, 0.1], [0.21, 0], [0, 0.1], [0, 0]])
    factors = np.array([1.06, 1.03, 1.15, 1.06, 1.01, 0.99])
    real_returns = (1 + returns) / factors[:, None] - 1
    weights = [0.65, 0.35]
    options = dict(
        transaction_fee_rate=0.002,
        capital_gains_tax_rate=0.25,
        tax_months=[10, 11, 12, 1, 2, 3],
        rebalancing_interval_months=1,
    )
    nominal = simulate_portfolio(returns, weights, **options)
    real = simulate_portfolio(real_returns, weights, inflation_factors=factors, **options)
    for mode in ("net", "gross"):
        np.testing.assert_allclose(
            real[f"{mode}_wealth"][1:],
            np.array(nominal[f"{mode}_wealth"][1:]) / factors.cumprod(),
            rtol=1e-11,
        )
    assert sum(e["capital_gains_tax"] > 0 for e in nominal["events"][1:]) == 2
    for event, real_event in zip(nominal["events"][1:], real["events"][1:], strict=True):
        scale = factors.cumprod()[event["month_index"]]
        for k in ("annual_realized_gain", "capital_gains_tax", "realized_capital_gain"):
            assert real_event[k] * scale == pytest.approx(event[k], abs=1e-12)
    for path in (nominal, real):
        events = path["events"]
        taxes = (
            sum(e.get("capital_gains_tax", 0) / e["pretrade_nav"] for e in events)
            * 12
            / len(returns)
        )
        fees = sum(e["commissions"] / e["pretrade_nav"] for e in events) * 12 / len(returns)
        assert path["annualized_capital_gains_tax"] == pytest.approx(taxes)
        assert path["annualized_commissions"] == pytest.approx(fees)
        assert (
            path["annualized_total_costs"]
            == path["annualized_commissions"] + path["annualized_capital_gains_tax"]
        )
        assert path["total_costs"] == path["total_commissions"] + path["total_capital_gains_tax"]
    data = np.stack([returns, real_returns], axis=1)[..., None, :]
    inflation = np.stack([np.ones_like(factors), factors], axis=1)[..., None, None]
    targets = np.broadcast_to(weights, data.shape)
    metrics = simulate_target_paths(data, weights, targets, inflation_factors=inflation, **options)
    for row, path in enumerate((nominal, real)):
        expected = [
            path[mode][key]
            for mode in ("net", "gross")
            for key in ("annualized_cagr", "maximum_drawdown", "annualized_volatility")
        ] + [path[key] for key in TARGET_PATH_METRICS[6:]]
        np.testing.assert_allclose(
            [metric[row, 0] for metric in metrics], expected, rtol=1e-10, atol=1e-13
        )


def test_synthetic_calendar_and_bootstrap_reproducibility(panel):
    period = pd.period_range("2000-04", periods=len(panel.values), freq="M")
    shifted = replace(
        panel,
        returns=panel.returns.set_axis(period),
        simulation_options={"capital_gains_tax_rate": 0.1},
    )
    splits = bootstrap_folds(len(period), 4, "sample")
    for seed, block in [(0, 3), (42, 12), (103, 24)]:
        sampled = list(bootstrap_generation(splits, block, seed, 0))
        drawn = np.concatenate([v for _, v in sampled])
        synthetic = shifted.path_options(drawn, synthetic=True)["tax_months"]
        np.testing.assert_array_equal(synthetic, period.month)
        assert not np.array_equal(synthetic, period.month[drawn])
        for (source, _), (draw, _) in zip(splits, sampled, strict=True):
            settings = shifted.path_options(draw, synthetic=True, calendar_start=int(source[0]))
            np.testing.assert_array_equal(settings["tax_months"], period.month[source])
    np.testing.assert_array_equal(
        shifted.path_options(splits[0][1])["tax_months"], period.month[splits[0][1]]
    )
    options = dict(
        bootstrap_iterations=3, cv_folds=4, objectives=["gmv"], transaction_fee_rate=0.002
    )
    untaxed = analyze_bootstrap(shifted, **options)
    taxed = analyze_bootstrap(shifted, capital_gains_tax_rate=0.1, **options)
    keys = (
        "sample_fingerprint",
        "validation_sample_fingerprint",
        "outer_folds",
        "random_seed",
        "scenario_counts",
        "sample_length",
    )
    assert {k: untaxed[k] for k in keys} == {k: taxed[k] for k in keys}
    assert taxed["objectives"]["gmv"]["distributions"]["total_capital_gains_tax"]["maximum"] > 0


def test_all_rebalancing_policies_share_calendar_and_buy_hold_does_not_pay_tax(panel):
    months = pd.period_range("2000-04", periods=len(panel.values), freq="M")
    panel = replace(panel, returns=panel.returns.set_axis(months))
    result = analyze_rebalancing(
        panel, capital_gains_tax_rate=0.1, user_weights=dict.fromkeys(panel.assets, 1 / 3)
    )
    expected = np.flatnonzero(
        (months.month == 12) | (np.arange(len(months)) == len(months) - 1)
    ).tolist()
    for path in result["strategies"]:
        assert path["tax_year_close_months"] == expected
        for event in path["events"]:
            if event.get("capital_gains_tax", 0):
                assert event["month_index"] in expected
        if path["id"] == "calendar_None":
            assert path["total_capital_gains_tax"] == 0
            assert len(path["events"]) == 1


def test_zero_tax_preserves_transactions_and_zero_tax_sales():
    options = dict(transaction_fee_rate=0.02, rebalancing_interval_months=1)
    values = [[0.1, 0], [0, -0.04], [0.2, 0]]
    path = simulate_portfolio(
        values, [0.6, 0.4], capital_gains_tax_rate=0, tax_months=[12, 1, 2], **options
    )
    previous = simulate_portfolio(values, [0.6, 0.4], **options)
    assert path == previous
    assert path["total_costs"] == path["total_commissions"]
    assert [e["kind"] for e in path["events"]] == [
        "initial_purchase",
        "calendar_rebalance",
        "calendar_rebalance",
    ]
