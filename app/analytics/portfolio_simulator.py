"""Monthly holdings simulation; signals and execution occur after observed returns."""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.analytics.models import TAX_METHODOLOGY, AnalyticsError

COST_METRICS = (
    "total_commissions",
    "annualized_commissions",
    "total_capital_gains_tax",
    "annualized_capital_gains_tax",
    "total_costs",
    "annualized_total_costs",
)
TRADING_METRICS = (
    *COST_METRICS,
    "trade_count",
    "rebalancing_events",
    "annualized_turnover",
    "execution_target_adjustment",
    "unpaid_capital_gains_tax",
)
TARGET_PATH_METRICS = (
    "net_cagr",
    "net_drawdown",
    "net_volatility",
    "gross_cagr",
    "gross_drawdown",
    "gross_volatility",
    "annualized_commissions",
    "annualized_capital_gains_tax",
    "annualized_total_costs",
    "total_commissions",
    "total_capital_gains_tax",
    "total_costs",
    "unpaid_capital_gains_tax",
)
TARGET_PATH_RATIO_METRICS = ("net_sharpe", "net_sortino", "gross_sharpe", "gross_sortino")


@dataclass(frozen=True)
class MonthlyState:
    elapsed_months: int
    positions: np.ndarray
    actual_weights: np.ndarray
    target_weights: np.ndarray
    asset_returns: np.ndarray


class MonthlySignal(Protocol):
    def __call__(self, state: MonthlyState) -> bool: ...


class RebalancingPolicy(Protocol):
    def triggers(self, state: MonthlyState) -> list[int] | None: ...


class TargetProvider(Protocol):
    def __call__(self, state: MonthlyState) -> np.ndarray: ...


class CapitalDestination(Protocol):
    def __call__(self, target: np.ndarray, state: MonthlyState) -> np.ndarray: ...


@dataclass(frozen=True)
class CalendarPolicy:
    interval: int | None = 12

    def __post_init__(self):
        if self.interval is not None and (type(self.interval) is not int or self.interval < 1):
            raise AnalyticsError("Calendar interval must be a positive integer or null.")

    def triggers(self, state):
        return [] if self.scheduled(state.elapsed_months) else None

    def scheduled(self, elapsed_months):
        return self.interval is not None and elapsed_months % self.interval == 0


@dataclass(frozen=True)
class ThresholdPolicy:
    threshold: float

    def __post_init__(self):
        if not np.isfinite(self.threshold) or self.threshold <= 0:
            raise AnalyticsError("Relative threshold must be finite and positive.")

    def triggers(self, state):
        positive = state.target_weights > 0
        relative = np.zeros_like(state.target_weights)
        relative[positive] = np.abs(
            state.actual_weights[positive] / state.target_weights[positive] - 1
        )
        hits = np.flatnonzero(positive & (relative >= self.threshold - 1e-12))
        return hits.tolist() if hits.size else None


def allocation(weights, size):
    w = np.asarray(weights, dtype=float)
    if w.shape != (size,) or not np.isfinite(w).all() or np.any(w < 0) or abs(w.sum() - 1) > 1e-8:
        raise AnalyticsError(
            "Simulation requires explicit finite nonnegative weights totaling 100%."
        )
    return w.copy()


class TradeExecutor(Protocol):
    def __call__(self, positions, target, wealth, fee_rate): ...


def execute_target(positions, target, wealth, fee_rate):
    """Solve V + fee*sum(abs(target*V - old_positions)) = pre-trade wealth.

    Positions are aggregate notionals per unique asset; buys and sells are netted
    before charging. Cash is spent exactly, including fees; no borrowing.
    """
    if positions.ndim > 1:
        # Each fixed set of buys/sells gives a linear equation for post-trade wealth.
        # Starting at pre-trade wealth, a buy can only switch to a sell.
        final_wealth = wealth
        if fee_rate:
            buys = target * wealth > positions
            for _ in range(positions.shape[-1] + 1):
                signs = np.where(buys, 1.0, -1.0)
                final_wealth = (wealth + fee_rate * (signs * positions).sum(-1, keepdims=True)) / (
                    1 + fee_rate * (signs * target).sum(-1, keepdims=True)
                )
                next_buys = target * final_wealth > positions
                if np.array_equal(next_buys, buys):
                    break
                buys = next_buys
        final = target * final_wealth
        trades = final - positions
        return final, trades, fee_rate * np.abs(trades).sum(-1)
    if wealth <= 0:
        return np.zeros_like(target), np.zeros_like(target), 0.0
    if fee_rate == 0:
        final = target * wealth
    else:
        low, high = 0.0, wealth
        for _ in range(48):
            mid = (low + high) / 2
            if mid + fee_rate * np.abs(target * mid - positions).sum() > wealth:
                high = mid
            else:
                low = mid
        final = target * ((low + high) / 2)
    trades = final - positions
    return final, trades, float(fee_rate * np.abs(trades).sum())


def _sale_fractions(positions, trades):
    return np.divide(
        np.maximum(-trades, 0), positions, out=np.zeros_like(positions), where=positions > 0
    )


def _average_cost_trade(positions, final, trades, basis, fee_rate):
    sold_basis = _sale_fractions(positions, trades) * basis
    gain = (np.maximum(-trades, 0) * (1 - fee_rate) - sold_basis).sum(axis=-1)
    remaining = np.where(final > 0, basis - sold_basis, 0)
    return gain, remaining + np.maximum(trades, 0) * (1 + fee_rate)


def _execute_taxable_target(
    positions, target, wealth, fee_rate, tax_rate, basis, annual_gain, executor=execute_target
):
    """Close a tax year and fund the liability with the same net trades as the target."""
    final, trades, fees = executor(positions, target, wealth, fee_rate)
    sale_gains = positions * (1 - fee_rate) - basis

    def taxable_gain(trades):
        return annual_gain + (_sale_fractions(positions, trades) * sale_gains).sum(axis=-1)

    taxable = taxable_gain(trades) > 0
    if np.any(taxable):
        # Tax reduces investable wealth and can turn buys into sells. Between
        # those breakpoints, the cash equation with positive tax is linear.
        upper = final.sum(axis=-1, keepdims=True)
        switches = np.divide(
            positions,
            target,
            out=np.broadcast_to(upper, positions.shape).copy(),
            where=target * upper > positions,
        )
        grid = np.concatenate((np.zeros_like(upper), np.sort(switches, axis=-1), upper), axis=-1)

        def residual(wealth_after):
            trial = target * wealth_after - positions
            trial_fees = fee_rate * np.abs(trial).sum(axis=-1)
            # A positive-tax solution is at or below the fee-only solution.
            # The residual is linear between trade breakpoints on that branch.
            tax = tax_rate * taxable_gain(trial)
            return wealth_after + np.expand_dims(trial_fees + tax, -1) - wealth

        lo = np.zeros(upper.shape, dtype=int)
        hi = np.full_like(lo, grid.shape[-1] - 1)
        while np.any(hi - lo > 1):
            mid = (lo + hi) // 2
            above = residual(np.take_along_axis(grid, mid, axis=-1)) > 0
            hi = np.where(above & (mid > lo), mid, hi)
            lo = np.where(~above, mid, lo)
        low = np.take_along_axis(grid, lo, axis=-1)
        high = np.take_along_axis(grid, hi, axis=-1)
        left, right = residual(low), residual(high)
        fraction = np.divide(-left, right - left, out=np.zeros_like(left), where=right > left)
        settled = low + (high - low) * np.clip(fraction, 0, 1)
        exhausted = residual(np.zeros_like(upper)) > 0
        settled = np.where(exhausted, 0, settled)
        final = np.where(np.expand_dims(taxable, -1), target * settled, final)
        trades = final - positions
        fees = fee_rate * np.abs(trades).sum(axis=-1)
    gain, basis = _average_cost_trade(positions, final, trades, basis, fee_rate)
    taxable_gain_at_close = np.maximum(annual_gain + gain, 0)
    liability = tax_rate * taxable_gain_at_close
    available = np.maximum(0, np.asarray(wealth).reshape(liability.shape) - fees)
    paid = np.where(final.sum(axis=-1) == 0, np.minimum(liability, available), liability)
    return final, trades, fees, paid, gain, basis, liability - paid


def _tax_calendar(length, tax_months, inflation_factors):
    months = np.arange(length) % 12 + 1 if tax_months is None else np.asarray(tax_months)
    if months.shape != (length,) or not np.isin(months, np.arange(1, 13)).all():
        raise AnalyticsError("Tax calendar needs a calendar month (1–12) for each observation.")
    closes = months == 12
    closes[-1] = True
    if inflation_factors is not None:
        factors = np.asarray(inflation_factors)
        if len(factors) != length or not np.isfinite(factors).all() or np.any(factors <= 0):
            raise AnalyticsError("Tax inflation factors must be positive and match path months.")
    return months, closes


def _validate_tax_rate(rate):
    if not np.isfinite(rate) or not 0 <= rate <= 1:
        raise AnalyticsError("capital_gains_tax_rate must be in [0,1].")


def _return_risk_statistics(monthly, initial_investment):
    n = monthly.shape[-1]
    with np.errstate(divide="ignore", over="ignore"):
        log_growth = np.cumsum(np.log1p(monthly), axis=-1)
        peaks = np.maximum(0.0, np.maximum.accumulate(log_growth, axis=-1))
        drawdown = -np.expm1(log_growth - peaks)
        cagr = np.expm1(log_growth[..., -1] * 12 / n)
    volatility = monthly.std(axis=-1, ddof=1) * np.sqrt(12) if n > 1 else np.zeros_like(cagr)
    return (
        cagr,
        volatility,
        np.maximum(drawdown.max(axis=-1), 1 - initial_investment),
        log_growth[..., -1],
    )


def _risk_adjusted_ratios(monthly, risk_free):
    differential = monthly - risk_free
    excess = np.sqrt(12) * differential.mean(axis=-1)
    sd = differential.std(axis=-1, ddof=1) if monthly.shape[-1] > 1 else np.zeros_like(excess)
    downside = np.sqrt(np.mean(np.minimum(differential, 0) ** 2, axis=-1))
    return tuple(
        np.divide(excess, risk, out=np.full_like(excess, np.nan), where=risk > 1e-12)
        for risk in (sd, downside)
    )


def path_statistics(monthly, wealth, risk_free, *, initial_investment=1.0):
    monthly, wealth, risk_free = map(
        lambda x: np.asarray(x, dtype=float), (monthly, wealth, risk_free)
    )
    sharpe, sortino = _risk_adjusted_ratios(monthly, risk_free)
    cagr, volatility, drawdown, log_terminal = _return_risk_statistics(monthly, initial_investment)
    with np.errstate(over="ignore"):
        terminal = float(np.exp(log_terminal))
    return {
        "annualized_cagr": float(cagr) if np.isfinite(cagr) else None,
        "calmar_ratio": float(cagr / drawdown)
        if drawdown > 0 and np.isfinite(cagr / drawdown)
        else None,
        "annualized_arithmetic_return": float(monthly.mean() * 12),
        "annualized_volatility": float(volatility),
        "annualized_downside_volatility": float(np.sqrt(12 * np.mean(np.minimum(monthly, 0) ** 2))),
        "historical_sharpe_ratio": float(sharpe) if np.isfinite(sharpe) else None,
        "sortino_ratio": float(sortino) if np.isfinite(sortino) else None,
        "maximum_drawdown": float(drawdown),
        "terminal_wealth": terminal if np.isfinite(terminal) else None,
    }


def simulate_target_paths(
    values,
    weights,
    targets,
    *,
    risk_free=None,
    transaction_fee_rate=0.0,
    capital_gains_tax_rate=0.0,
    inflation_factors=None,
    tax_months=None,
    rebalancing_interval_months=12,
):
    """Run known end-of-month targets across scenarios, retaining only summary metrics.

    Values and targets are month-major, with broadcast-compatible scenario dimensions
    between the month and asset axes. Targets depend only on observed asset returns.
    """
    if not len(values):
        raise AnalyticsError("Simulation needs at least one monthly observation.")
    policy = CalendarPolicy(rebalancing_interval_months)
    if not np.isfinite(transaction_fee_rate) or not 0 <= transaction_fee_rate < 1:
        raise AnalyticsError("transaction_fee_rate must be in [0,1).")
    _validate_tax_rate(capital_gains_tax_rate)
    target = allocation(weights, values.shape[-1])
    positions, _, initial_fee = execute_target(
        np.zeros_like(target), target, 1.0, transaction_fee_rate
    )
    initial_investment = positions.sum()
    basis = (
        np.broadcast_to(positions * (1 + transaction_fee_rate), targets.shape[1:]).copy()
        if capital_gains_tax_rate
        else None
    )
    gross_positions = target.copy()
    fee_fractions = np.full(targets.shape[1:-1], initial_fee)
    fees = fee_fractions.copy()
    tax = np.zeros_like(fees)
    tax_fractions = np.zeros_like(fees)
    unpaid = np.zeros_like(fees)
    annual_gain = np.zeros_like(fees) if basis is not None else None
    if basis is not None:
        _, tax_closes = _tax_calendar(len(values), tax_months, inflation_factors)
        if inflation_factors is not None:
            inflation_factors = np.asarray(inflation_factors, dtype=float)
            if inflation_factors.ndim > 1 and inflation_factors.shape[-1] != 1:
                raise AnalyticsError("Inflation must be shared by all assets within a path.")
    monthly = np.empty((*fee_fractions.shape, len(values)))
    gross_monthly = np.empty_like(monthly)
    previous_wealth = 1.0
    previous_gross_wealth = 1.0
    for month, (returns, desired) in enumerate(zip(values, targets, strict=True)):
        if basis is not None and inflation_factors is not None:
            basis /= inflation_factors[month]
            annual_gain /= np.reshape(
                inflation_factors[month], np.shape(inflation_factors[month])[:-1]
            )
        positions = positions * (1 + returns)
        gross_positions = gross_positions * (1 + returns)
        wealth = positions.sum(axis=-1, keepdims=True)
        close = basis is not None and tax_closes[month]
        if month + 1 < len(values) or close:
            actual = np.divide(positions, wealth, out=np.zeros_like(positions), where=wealth > 0)
            if month + 1 < len(values):
                signal = (
                    policy.scheduled(month + 1)
                    | (np.max(np.abs(desired - target), axis=-1) > 1e-10)
                ) & (wealth[..., 0] > 0)
                trade = signal & (np.max(np.abs(actual - desired), axis=-1) > 1e-12)
                target = np.where(signal[..., None], desired, target)
            else:
                trade = np.zeros(fees.shape, dtype=bool)
            execution = (trade | (annual_gain > 0)) if close else trade
            if np.any(execution):
                pretrade_wealth = np.broadcast_to(wealth, (*trade.shape, 1))[execution]
                execution_targets = (
                    np.where(trade[..., None], desired, actual)[execution]
                    if close
                    else desired[execution]
                )
                if positions.shape != desired.shape:
                    positions = np.broadcast_to(positions, desired.shape).copy()
                old = positions[execution]
                if close:
                    final, _, cost, paid, _, basis[execution], shortfall = _execute_taxable_target(
                        old,
                        execution_targets,
                        pretrade_wealth,
                        transaction_fee_rate,
                        capital_gains_tax_rate,
                        basis[execution],
                        annual_gain[execution],
                    )
                    tax[execution] += paid
                    unpaid[execution] += shortfall
                    nav = pretrade_wealth[:, 0]
                    tax_fractions[execution] += np.divide(
                        paid, nav, out=np.zeros_like(paid), where=nav > 0
                    )
                else:
                    final, trades, cost = execute_target(
                        old, execution_targets, pretrade_wealth, transaction_fee_rate
                    )
                    if basis is not None:
                        gain, basis[execution] = _average_cost_trade(
                            old, final, trades, basis[execution], transaction_fee_rate
                        )
                        annual_gain[execution] += gain
                positions[execution] = final
                fees[execution] += cost
                nav = pretrade_wealth[:, 0]
                fee_fractions[execution] += (
                    np.divide(cost, nav, out=np.zeros_like(cost), where=nav > 0)
                    if close
                    else cost / nav
                )
                wealth = positions.sum(axis=-1, keepdims=True)
            if np.any(trade):
                # Apply the same executions to gross holdings without deducting costs.
                if gross_positions.shape != desired.shape:
                    gross_positions = np.broadcast_to(gross_positions, desired.shape).copy()
                gross_positions[trade] = desired[trade] * gross_positions[trade].sum(
                    axis=-1, keepdims=True
                )
            if close:
                annual_gain.fill(0)
        wealth = wealth[..., 0]
        if not np.isfinite(wealth).all():
            raise AnalyticsError("Portfolio wealth overflowed; shorten the selected horizon.")
        monthly[..., month] = (
            np.divide(wealth, previous_wealth, out=np.ones_like(wealth), where=previous_wealth > 0)
            - 1
        )
        previous_wealth = wealth
        gross_wealth = gross_positions.sum(axis=-1)
        gross_monthly[..., month] = (
            np.divide(
                gross_wealth,
                previous_gross_wealth,
                out=np.ones_like(gross_wealth),
                where=previous_gross_wealth > 0,
            )
            - 1
        )
        previous_gross_wealth = gross_wealth
    cagr, volatility, drawdown, _ = _return_risk_statistics(monthly, initial_investment)
    gross_cagr, gross_volatility, gross_drawdown, _ = _return_risk_statistics(gross_monthly, 1.0)
    annualized_fees = fee_fractions * (12 / len(values))
    annualized_tax = tax_fractions * (12 / len(values))
    metrics = (
        cagr,
        drawdown,
        volatility,
        gross_cagr,
        gross_drawdown,
        gross_volatility,
        annualized_fees,
        annualized_tax,
        annualized_fees + annualized_tax,
        fees,
        tax,
        fees + tax,
        unpaid,
    )
    if risk_free is None:
        return metrics
    return (
        metrics
        + _risk_adjusted_ratios(monthly, risk_free)
        + _risk_adjusted_ratios(gross_monthly, risk_free)
    )


def _execution_allocation(requested, assets, allow_solver_residuals):
    requested = np.asarray(requested, dtype=float)
    if (
        allow_solver_residuals
        and requested.shape == (assets,)
        and np.isfinite(requested).all()
        and requested.min() >= -1e-7
        and abs(requested.sum() - 1) <= 1e-7
        and np.any(requested < 0)
    ):
        execution = np.maximum(requested, 0)
        return allocation(execution / execution.sum(), assets)
    return allocation(requested, assets)


def simulate_portfolio(
    values,
    weights,
    risk_free=None,
    *,
    policy=None,
    transaction_fee_rate=0.0,
    capital_gains_tax_rate=0.0,
    inflation_factors=None,
    tax_months=None,
    rebalancing_interval_months=12,
    target_provider=None,
    capital_destination=None,
    executor=execute_target,
    record_history=True,
    allow_solver_residuals=False,
    signal_generator=None,
    target_schedule=None,
    signal_returns=None,
):
    """Value holdings with values and feed aligned signal_returns to decision rules."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or not len(values) or not np.isfinite(values).all() or np.any(values < -1):
        raise AnalyticsError("Simulation needs finite monthly asset returns >= -100%.")
    n, assets = values.shape
    if signal_returns is None or signal_returns is values:
        signal_returns = values
    else:
        signal_returns = np.asarray(signal_returns, dtype=float)
        if (
            signal_returns.shape != values.shape
            or not np.isfinite(signal_returns).all()
            or np.any(signal_returns < -1)
        ):
            raise AnalyticsError("Signal returns must match the simulation months and assets.")
    requested = np.asarray(weights, dtype=float)
    target = _execution_allocation(requested, assets, allow_solver_residuals)
    # Keys are elapsed months. Execute after that observation, before the next fold.
    schedule = {
        month: _execution_allocation(new_target, assets, allow_solver_residuals)
        for month, new_target in (target_schedule or {}).items()
    }
    if any(not 0 < month < n for month in schedule):
        raise AnalyticsError("Scheduled portfolio weights must start inside the simulation.")
    scheduled_adjustment = None
    if schedule:
        scheduled_adjustment = max(
            np.max(np.abs(target - requested)),
            *(
                np.max(np.abs(execution - target_schedule[month]))
                for month, execution in schedule.items()
            ),
        )
    if not np.isfinite(transaction_fee_rate) or not 0 <= transaction_fee_rate < 1:
        raise AnalyticsError("transaction_fee_rate must be in [0,1).")
    _validate_tax_rate(capital_gains_tax_rate)
    rf = np.zeros(n) if risk_free is None else np.asarray(risk_free, dtype=float)
    if rf.shape != (n,) or not np.isfinite(rf).all():
        raise AnalyticsError("Risk-free months must match simulation months.")
    policy = policy if policy is not None else CalendarPolicy(rebalancing_interval_months)
    tax_closes = None
    if capital_gains_tax_rate:
        calendar, tax_closes = _tax_calendar(n, tax_months, inflation_factors)
        if inflation_factors is not None:
            inflation_factors = np.asarray(inflation_factors, dtype=float)
            if inflation_factors.shape != (n,):
                raise AnalyticsError("Inflation must provide one factor per simulation month.")
    initial_fee = transaction_fee_rate / (1 + transaction_fee_rate)
    tax_details = {
        "tax_methodology": TAX_METHODOLOGY,
        "tax_year_close_months": np.flatnonzero(tax_closes).tolist()
        if record_history and tax_closes is not None
        else [],
    }
    if (
        np.count_nonzero(target) == 1
        and not target_provider
        and not capital_destination
        and not signal_generator
        and not schedule
        and not record_history
    ):
        column = values[:, np.flatnonzero(target)[0]].copy()
        dead = np.flatnonzero(column == -1)
        if dead.size:
            column[dead[0] + 1 :] = 0
        net_column = column.copy()
        net_column[0] = (1 + column[0]) / (1 + transaction_fee_rate) - 1

        def wealth_path(returns):
            with np.errstate(divide="ignore", over="ignore"):
                wealth = np.exp(np.r_[0.0, np.cumsum(np.log1p(returns))])
            return [float(v) if np.isfinite(v) else None for v in wealth]

        return {
            "net": path_statistics(
                net_column, [], rf, initial_investment=1 / (1 + transaction_fee_rate)
            ),
            "gross": path_statistics(column, [], rf),
            "net_returns": net_column.tolist(),
            "gross_returns": column.tolist(),
            "net_wealth": wealth_path(net_column),
            "gross_wealth": wealth_path(column),
            "total_commissions": initial_fee,
            "total_capital_gains_tax": 0.0,
            "annualized_commissions": initial_fee * 12 / n,
            "annualized_capital_gains_tax": 0.0,
            "total_costs": initial_fee,
            "annualized_total_costs": initial_fee * 12 / n,
            "unpaid_capital_gains_tax": 0.0,
            "tax_insolvent": False,
            **tax_details,
            "initial_net_wealth": 1 / (1 + transaction_fee_rate),
            "trade_count": 1,
            "rebalancing_events": 0,
            "annualized_turnover": 0.0,
            "execution_target_adjustment": float(np.max(np.abs(target - requested))),
            "maximum_weight_deviation": 1.0 if dead.size else 0.0,
            "events": [],
            "weight_history": [],
            "pretrade_weight_history": [],
            "positions": [],
            "target_history": [],
        }
    positions, initial_trades, fees = executor(np.zeros(assets), target, 1.0, transaction_fee_rate)
    basis = positions * (1 + transaction_fee_rate) if capital_gains_tax_rate else None
    annual_gain = total_tax = tax_fractions = unpaid_tax = 0.0
    fee_fractions = fees
    initial_investment = float(positions.sum())
    gross_positions = target.copy()
    net, gross, net_wealth, gross_wealth = [], [], [1.0], [1.0]
    history, pretrade_history, position_history, targets, events = [], [], [], [], []
    signal_history = []
    trade_count = int(np.count_nonzero(initial_trades))
    turnover, maximum_deviation, event_count = 0.0, 0.0, 0
    if record_history:
        events.append(
            {
                "month_index": -1,
                "calendar_month": (int(calendar[0]) - 2) % 12 + 1
                if tax_closes is not None
                else None,
                "kind": "initial_purchase",
                "triggering_assets": [],
                "trades": initial_trades.tolist(),
                "reason": "initial_purchase",
                "pretrade_nav": 1.0,
                "posttrade_nav": initial_investment,
                "purchases": float(initial_trades.sum()),
                "sales": 0.0,
                "realized_capital_gain": 0.0,
                "annual_realized_gain": 0.0,
                "cost_basis": basis.tolist() if basis is not None else None,
                "commissions": fees,
                "turnover": float(np.abs(initial_trades).sum()),
            }
        )
        position_history.append(positions.tolist())
    for month, returns in enumerate(values):
        if basis is not None and inflation_factors is not None:
            # Express both carry balances in the current month's purchasing power.
            basis = basis / inflation_factors[month]
            annual_gain /= inflation_factors[month]
        positions = positions * (1 + returns)
        gross_positions = gross_positions * (1 + returns)
        wealth = float(positions.sum())
        actual = positions / wealth if wealth > 0 else np.zeros(assets)
        maximum_deviation = max(maximum_deviation, float(np.max(np.abs(actual - target))))
        if record_history:
            pretrade_history.append(actual.tolist())
        state = MonthlyState(
            month + 1, positions.copy(), actual.copy(), target.copy(), signal_returns[month].copy()
        )
        signal = policy.triggers(state) if wealth > 0 else None
        desired = allocation(target_provider(state), assets) if target_provider else target
        scheduled = schedule.get(month + 1)
        if scheduled is not None:
            desired = scheduled
        dynamic_change = (
            not np.array_equal(desired, target)
            if scheduled is not None
            else target_provider is not None and np.max(np.abs(desired - target)) > 1e-10
        )
        if dynamic_change and wealth > 0:
            signal = [] if signal is None else signal
        if record_history and target_provider and hasattr(target_provider, "diagnostics"):
            signal_history.append(dict(target_provider.diagnostics))
        external_signal = signal_generator and signal_generator(state) and wealth > 0
        if external_signal:
            signal = [] if signal is None else signal
        # No terminal rebalance: there is no following observation to invest in.
        new_target = actual
        rebalance = False
        if signal is not None and month + 1 < n:
            new_target = desired
            if capital_destination:
                new_target = capital_destination(new_target, state)
            new_target = allocation(new_target, assets)
            rebalance = np.max(np.abs(actual - new_target)) > 1e-12
            target = new_target
        close = tax_closes is not None and tax_closes[month]
        if rebalance or (close and annual_gain > 0):
            old = positions
            tax = gain = unpaid = 0.0
            if close:
                positions, trades, cost, tax, gain, basis, unpaid = _execute_taxable_target(
                    old,
                    new_target if rebalance else actual,
                    wealth,
                    transaction_fee_rate,
                    capital_gains_tax_rate,
                    basis,
                    annual_gain,
                    executor,
                )
            else:
                positions, trades, cost = executor(old, new_target, wealth, transaction_fee_rate)
                if basis is not None:
                    gain, basis = _average_cost_trade(
                        old, positions, trades, basis, transaction_fee_rate
                    )
            annual_gain += gain
            total_tax += float(tax)
            unpaid_tax += float(unpaid)
            ratio = float(np.abs(trades).sum()) / wealth if wealth > 0 else 0.0
            turnover += ratio
            fees += cost
            if wealth > 0:
                fee_fractions += cost / wealth
                tax_fractions += tax / wealth
            trade_count += int(np.count_nonzero(np.abs(trades) > wealth * 1e-12))
            if rebalance:
                gross_positions = new_target * gross_positions.sum()
                event_count += 1
            if record_history:
                if rebalance:
                    reason = (
                        "fold_target_change"
                        if scheduled is not None and dynamic_change
                        else "control_change"
                        if dynamic_change or external_signal
                        else "threshold_rebalance"
                        if isinstance(policy, ThresholdPolicy)
                        else "calendar_rebalance"
                    )
                else:
                    reason = "terminal_tax_settlement" if month + 1 == n else "tax_settlement"
                events.append(
                    {
                        "month_index": month,
                        "calendar_month": int(calendar[month]) if tax_closes is not None else None,
                        "kind": "combined_rebalance_and_tax_settlement"
                        if close and rebalance
                        else reason,
                        "reason": reason,
                        "triggering_assets": signal if rebalance else [],
                        "trades": trades.tolist(),
                        "purchases": float(np.maximum(trades, 0).sum()),
                        "sales": float(np.maximum(-trades, 0).sum()),
                        "pretrade_nav": wealth,
                        "posttrade_nav": float(positions.sum()),
                        "turnover": ratio,
                        "commissions": float(cost),
                        "realized_capital_gain": float(gain),
                        "annual_realized_gain": float(annual_gain),
                        "taxable_capital_gain": float(max(0, annual_gain)) if close else None,
                        "capital_gains_tax": float(tax),
                        "unpaid_capital_gains_tax": float(unpaid),
                        "tax_insolvent": bool(unpaid > 0),
                        "cost_basis": basis.tolist() if basis is not None else None,
                        "control_state": dict(getattr(target_provider, "diagnostics", {})),
                    }
                )
        if close:
            annual_gain = 0.0
        final, gross_final = float(positions.sum()), float(gross_positions.sum())
        if not np.isfinite(final) or not np.isfinite(gross_final):
            raise AnalyticsError("Portfolio wealth overflowed; shorten the selected horizon.")
        net.append(final / net_wealth[-1] - 1 if net_wealth[-1] > 0 else 0.0)
        gross.append(gross_final / gross_wealth[-1] - 1 if gross_wealth[-1] > 0 else 0.0)
        net_wealth.append(final)
        gross_wealth.append(gross_final)
        if record_history:
            history.append((positions / final if final > 0 else np.zeros(assets)).tolist())
            position_history.append(positions.tolist())
            targets.append(target.tolist())
    annualized_fees = fee_fractions * (12 / n)
    annualized_tax = float(tax_fractions) * (12 / n)
    return {
        "net": path_statistics(net, net_wealth, rf, initial_investment=initial_investment),
        "gross": path_statistics(gross, gross_wealth, rf),
        "net_returns": net,
        "gross_returns": gross,
        "net_wealth": net_wealth,
        "gross_wealth": gross_wealth,
        "weight_history": history,
        "pretrade_weight_history": pretrade_history,
        "positions": position_history,
        "target_history": targets,
        "events": events,
        "signal_history": signal_history,
        "total_commissions": fees,
        "total_capital_gains_tax": total_tax,
        "annualized_commissions": annualized_fees,
        "annualized_capital_gains_tax": annualized_tax,
        "total_costs": fees + total_tax,
        "annualized_total_costs": annualized_fees + annualized_tax,
        "unpaid_capital_gains_tax": unpaid_tax,
        "tax_insolvent": unpaid_tax > 0,
        **tax_details,
        "initial_net_wealth": initial_investment,
        "execution_target_adjustment": float(
            np.max(np.abs(target - requested))
            if scheduled_adjustment is None
            else scheduled_adjustment
        ),
        "trade_count": trade_count,
        "rebalancing_events": event_count,
        "annualized_turnover": turnover * 12 / n,
        "maximum_weight_deviation": maximum_deviation,
        "conventions": {
            "initial_purchases": "self_financing_included_in_first_month_net_return",
            "turnover": "two_sided_notional_excluding_initial_purchase_per_year",
            "terminal_rebalance": "omitted_without_following_month",
            "sortino": "monthly_portfolio_minus_aligned_rf; downside_RMS_over_all_months",
            "volatility": "sample_std_ddof_1_times_sqrt_12",
        },
    }
