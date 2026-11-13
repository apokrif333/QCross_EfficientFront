"""Monthly holdings simulation; signals and execution occur after observed returns."""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.analytics.models import AnalyticsError


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
        return [] if self.interval and state.elapsed_months % self.interval == 0 else None


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


def path_statistics(monthly, wealth, risk_free, *, initial_investment=1.0):
    monthly, wealth, risk_free = map(
        lambda x: np.asarray(x, dtype=float), (monthly, wealth, risk_free)
    )
    n = len(monthly)
    differential = monthly - risk_free
    sd = float(differential.std(ddof=1)) if n > 1 else 0
    downside = float(np.sqrt(np.mean(np.minimum(differential, 0) ** 2)))
    with np.errstate(divide="ignore"):
        log_path = np.r_[0.0, np.cumsum(np.log1p(monthly))]
    drawdown = -np.expm1(log_path - np.maximum.accumulate(log_path))
    with np.errstate(over="ignore"):
        terminal = float(np.exp(log_path[-1]))
        cagr = float(np.expm1(log_path[-1] * 12 / n))
    return {
        "annualized_cagr": cagr if np.isfinite(cagr) else None,
        "annualized_arithmetic_return": float(monthly.mean() * 12),
        "annualized_volatility": float(monthly.std(ddof=1) * np.sqrt(12)) if n > 1 else 0.0,
        "historical_sharpe_ratio": float(np.sqrt(12) * differential.mean() / sd)
        if sd > 1e-12
        else None,
        "sortino_ratio": float(np.sqrt(12) * differential.mean() / downside)
        if downside > 1e-12
        else None,
        "maximum_drawdown": max(float(drawdown.max()), 1 - initial_investment),
        "terminal_wealth": terminal if np.isfinite(terminal) else None,
    }


def simulate_portfolio(
    values,
    weights,
    risk_free=None,
    *,
    policy=None,
    transaction_fee_rate=0.0,
    rebalancing_interval_months=12,
    target_provider=None,
    capital_destination=None,
    executor=execute_target,
    record_history=True,
    allow_solver_residuals=False,
    signal_generator=None,
):
    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or not len(values) or not np.isfinite(values).all() or np.any(values < -1):
        raise AnalyticsError("Simulation needs finite monthly asset returns >= -100%.")
    n, assets = values.shape
    requested = np.asarray(weights, dtype=float)
    execution = requested
    if (
        allow_solver_residuals
        and requested.shape == (assets,)
        and np.isfinite(requested).all()
        and requested.min() >= -1e-7
        and abs(requested.sum() - 1) <= 1e-7
        and np.any(requested < 0)
    ):
        execution = np.maximum(requested, 0)
        execution = execution / execution.sum()
    target = allocation(execution, assets)
    if not np.isfinite(transaction_fee_rate) or not 0 <= transaction_fee_rate < 1:
        raise AnalyticsError("transaction_fee_rate must be in [0,1).")
    rf = np.zeros(n) if risk_free is None else np.asarray(risk_free, dtype=float)
    if rf.shape != (n,) or not np.isfinite(rf).all():
        raise AnalyticsError("Risk-free months must match simulation months.")
    policy = policy if policy is not None else CalendarPolicy(rebalancing_interval_months)
    if (
        np.count_nonzero(target) == 1
        and not target_provider
        and not capital_destination
        and not signal_generator
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
            "total_commissions": transaction_fee_rate / (1 + transaction_fee_rate),
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
                "kind": "initial_purchase",
                "triggering_assets": [],
                "trades": initial_trades.tolist(),
                "commissions": fees,
                "turnover": float(np.abs(initial_trades).sum()),
            }
        )
        position_history.append(positions.tolist())
    for month, returns in enumerate(values):
        positions = positions * (1 + returns)
        gross_positions = gross_positions * (1 + returns)
        wealth = float(positions.sum())
        actual = positions / wealth if wealth > 0 else np.zeros(assets)
        maximum_deviation = max(maximum_deviation, float(np.max(np.abs(actual - target))))
        if record_history:
            pretrade_history.append(actual.tolist())
        state = MonthlyState(
            month + 1, positions.copy(), actual.copy(), target.copy(), returns.copy()
        )
        signal = policy.triggers(state) if wealth > 0 else None
        desired = allocation(target_provider(state), assets) if target_provider else target
        dynamic_change = np.max(np.abs(desired - target)) > 1e-10
        if dynamic_change and wealth > 0:
            signal = [] if signal is None else signal
        if target_provider and hasattr(target_provider, "diagnostics"):
            signal_history.append(dict(target_provider.diagnostics))
        if signal_generator and signal_generator(state) and wealth > 0:
            signal = [] if signal is None else signal
        # No terminal rebalance: there is no following observation to invest in.
        if signal is not None and month + 1 < n:
            new_target = desired
            if capital_destination:
                new_target = capital_destination(new_target, state)
            new_target = allocation(new_target, assets)
            if np.max(np.abs(actual - new_target)) > 1e-12:
                positions, trades, cost = executor(
                    positions, new_target, wealth, transaction_fee_rate
                )
                gross_positions = new_target * gross_positions.sum()
                traded = float(np.abs(trades).sum())
                ratio = traded / wealth
                turnover += ratio
                fees += cost
                trade_count += int(np.count_nonzero(np.abs(trades) > wealth * 1e-12))
                event_count += 1
                if record_history:
                    events.append(
                        {
                            "month_index": month,
                            "kind": "rebalance",
                            "triggering_assets": signal,
                            "trades": trades.tolist(),
                            "turnover": ratio,
                            "commissions": cost,
                            "reason": "control_change" if dynamic_change else "rebalancing_policy",
                        }
                    )
            target = new_target
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
        "initial_net_wealth": initial_investment,
        "execution_target_adjustment": float(np.max(np.abs(target - requested))),
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
