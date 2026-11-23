"""Sequential user-sleeve controls, independent of optimizer moments."""

import numpy as np

from app.analytics.models import AnalyticsError
from app.analytics.portfolio_simulator import simulate_portfolio, simulate_target_paths
from app.analytics.provenance import panel_provenance

TREND_LOOKBACKS = range(1, 37)


def _sma_active(window):
    peak = window.max(axis=-1)
    return np.exp(window[..., -1] - peak) >= np.exp(window - peak[..., None]).mean(axis=-1)


def _trend_weights(base, hedge, active, destination):
    kept = base * active
    mass = kept.sum(axis=-1, keepdims=True)
    if destination == "remaining":
        use_hedge = mass <= 0
        desired = np.divide(kept, mass, out=np.zeros_like(kept, dtype=float), where=~use_hedge)
        return desired + use_hedge * hedge, use_hedge
    hedge_fraction = np.maximum(0.0, 1 - mass)
    return kept + hedge_fraction * hedge, hedge_fraction


class DownsideTarget:
    def __init__(self, base, hedge, target, lookback):
        self.base, self.hedge = base, hedge
        self.target, self.lookback = target, lookback
        self.history = []
        self.diagnostics = {}

    def __call__(self, state):
        self.history.append(float(self.base @ state.asset_returns))
        ready = len(self.history) >= self.lookback
        downside = (
            float(np.sqrt(12 * np.mean(np.minimum(self.history[-self.lookback :], 0) ** 2)))
            if ready
            else None
        )
        alpha = min(1.0, self.target / downside) if downside else 1.0
        self.diagnostics = {
            "alpha": alpha,
            "downside_volatility": downside,
            "hedge_allocation": 1 - alpha,
            "warmup": not ready,
            "target_downside_volatility": self.target,
        }
        return alpha * self.base + (1 - alpha) * self.hedge


class TrendTarget:
    def __init__(self, base, hedge, assets, rules, destination):
        self.base, self.hedge, self.assets = base, hedge, assets
        self.rules, self.destination = rules, destination
        self.prices = [np.zeros(len(base))]  # log TRI, initial capital index included
        self.previous_active = np.ones(len(base), dtype=bool)
        self.diagnostics = {}

    def __call__(self, state):
        with np.errstate(divide="ignore"):
            self.prices.append(self.prices[-1] + np.log1p(state.asset_returns))
        active = np.ones(len(self.base), dtype=bool)
        signals = {}
        for i, asset in enumerate(self.assets):
            rule = self.rules.get(asset, {"indicator": "disabled"})
            indicator = rule["indicator"]
            lookback = rule.get("lookback_months", 12)
            ready = len(self.prices) - 1 >= lookback
            if indicator != "disabled" and ready:
                current = self.prices[-1][i]
                if not np.isfinite(current):
                    active[i] = False
                elif indicator == "momentum":
                    active[i] = current >= self.prices[-1 - lookback][i]
                else:
                    window = np.array(self.prices[-lookback:])[:, i]
                    # Scale the whole window; avoids overflow of long-run TRI.
                    active[i] = _sma_active(window)
            signals[asset] = {
                "active": bool(active[i]),
                "warmup": indicator != "disabled" and not ready,
            }
        desired, hedge_allocation = _trend_weights(self.base, self.hedge, active, self.destination)
        hedge_fraction = float(hedge_allocation[0])
        self.diagnostics = {
            "signals": signals,
            "active_assets": [
                a
                for a, i in zip(self.assets, range(len(active)), strict=True)
                if active[i] and self.base[i] > 0
            ],
            "alpha": 1 - hedge_fraction,
            "hedge_allocation": hedge_fraction,
            "entries": [
                a for i, a in enumerate(self.assets) if active[i] and not self.previous_active[i]
            ],
            "exits": [
                a for i, a in enumerate(self.assets) if not active[i] and self.previous_active[i]
            ],
        }
        self.previous_active = active.copy()
        return desired


def _control_inputs(panel, user_weights, indices=None):
    if not user_weights or panel.control_returns is None:
        raise AnalyticsError("Portfolio controls require explicit weights and aligned hedge data.")
    frame = panel.control_returns
    values = frame.to_numpy() if indices is None else frame.to_numpy()[indices]
    base = np.array([user_weights.get(a, 0) for a in frame.columns])
    hedge = np.array([panel.control_config["hedge_weights"].get(a, 0) for a in frame.columns])
    rf = panel.risk_free_returns
    if rf is None:
        rate = panel.reference_metadata.get("fixed_annual_rate", 0.03)
        rf = np.full(len(frame), (1 + rate) ** (1 / 12) - 1)
    if indices is not None:
        rf = rf[indices]
    return values, base, hedge, rf, list(frame.columns)


def compare_controls(
    panel, user_weights, indices=None, *, record_history=True, policy=None, include_wealth=False
):
    if panel.control_config.get("mode", "none") == "none":
        return None
    values, base, hedge, rf, assets = _control_inputs(panel, user_weights, indices)
    settings = {**panel.simulation_options, "record_history": record_history, "policy": policy}
    if panel.control_config["mode"] == "downside_vol":
        params = panel.control_config["downside_vol"]
        provider = DownsideTarget(base, hedge, params["target_annual"], params["lookback_months"])
    else:
        params = panel.control_config["trend"]
        provider = TrendTarget(base, hedge, assets, params["asset_rules"], params["destination"])
    baseline = simulate_portfolio(values, base, rf, **settings)
    controlled = simulate_portfolio(values, base, rf, target_provider=provider, **settings)
    if not record_history:
        keys = (
            "net",
            "gross",
            "total_commissions",
            "trade_count",
            "annualized_turnover",
            "rebalancing_events",
        )
        baseline = {k: baseline[k] for k in keys}
        controlled_keys = (*keys, "net_wealth") if include_wealth else keys
        controlled = {k: controlled[k] for k in controlled_keys}
    return {
        "assets": assets,
        "config": panel.control_config,
        "baseline": baseline,
        "controlled": controlled,
        "interpretation": "Exposure scaling is not a guaranteed realized risk cap.",
    }


def _enabled_trend_rules(rules, user_weights):
    return {
        asset: rule
        for asset, rule in rules.items()
        if user_weights.get(asset, 0) > 0 and rule["indicator"] != "disabled"
    }


def _bootstrap_trend_signals(values, assets, rules):
    """End-of-month signals for every generation and lookback on the same paths."""
    months, draws, _ = values.shape
    signals = np.ones((months, draws, len(TREND_LOOKBACKS), len(assets)), dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        prices = np.concatenate((np.zeros_like(values[:1]), np.cumsum(np.log1p(values), axis=0)))
        for i, asset in enumerate(assets):
            if asset not in rules:
                continue
            price = prices[..., i]
            for period in TREND_LOOKBACKS:
                if period > months:
                    break
                current = price[period:]
                if rules[asset]["indicator"] == "momentum":
                    active = current >= price[:-period]
                else:
                    window = np.lib.stride_tricks.sliding_window_view(price[1:], period, axis=0)
                    active = _sma_active(window)
                signals[period - 1 :, :, period - 1, i] = np.isfinite(current) & active
    return signals


def has_enabled_trend_rules(panel, user_weights):
    return (
        panel.control_config.get("mode") == "trend"
        and bool(user_weights)
        and bool(_enabled_trend_rules(panel.control_config["trend"]["asset_rules"], user_weights))
    )


def analyze_bootstrap_trend_sensitivity(panel, *, user_weights, _sample_indices):
    """Compare common lookbacks on the original Bootstrap draws."""
    if not has_enabled_trend_rules(panel, user_weights):
        raise AnalyticsError("No positive-weight assets with SMA or Momentum enabled.")
    sample_indices = _sample_indices
    trend = panel.control_config["trend"]
    rules = _enabled_trend_rules(trend["asset_rules"], user_weights)
    values, base, hedge, _, assets = _control_inputs(panel, user_weights)
    asset_columns = {asset: assets.index(asset) for asset in rules}
    portfolios = {None: base}
    for asset, column in asset_columns.items():
        weights = np.zeros_like(base)
        weights[column] = 1
        portfolios[asset] = weights
    results = {
        asset: np.empty((len(TREND_LOOKBACKS), len(sample_indices), 7)) for asset in portfolios
    }
    # Bound the temporary target arrays even for long histories.
    batch_size = max(1, min(64, 1_000_000 // (len(values) * len(TREND_LOOKBACKS) * len(assets))))
    for start in range(0, len(sample_indices), batch_size):
        stop = min(start + batch_size, len(sample_indices))
        paths = values[sample_indices[start:stop]].transpose(1, 0, 2)
        signals = _bootstrap_trend_signals(paths, assets, rules)
        for asset, weights in portfolios.items():
            included = (weights > 0) | (hedge > 0)
            active = signals[..., included]
            targets, _ = _trend_weights(
                weights[included], hedge[included], active, trend["destination"]
            )
            metrics = simulate_target_paths(
                paths[..., None, included],
                weights[included],
                targets,
                **panel.simulation_options,
            )
            results[asset][:, start:stop] = np.stack(metrics, axis=-1).transpose(1, 0, 2)

    def export(samples):
        return np.where(np.isfinite(samples), samples, None).tolist()

    return {
        "trend_sensitivity": {
            "iterations": len(sample_indices),
            "commission_basis": "pretrade_nav",
            "portfolio": export(results[None]),
            "by_asset": {asset: export(results[asset]) for asset in rules},
        },
        "reproducibility": panel_provenance(panel),
    }


def _trend_sensitivity(panel, user_weights, configured_path):
    values, base, hedge, rf, assets = _control_inputs(panel, user_weights)
    trend = panel.control_config["trend"]
    rules = trend["asset_rules"]
    enabled_rules = _enabled_trend_rules(rules, user_weights)
    if not enabled_rules:
        return []
    configured_periods = {rule["lookback_months"] for rule in enabled_rules.values()}
    points = []
    for lookback in TREND_LOOKBACKS:
        path = configured_path
        if configured_periods != {lookback}:
            varied_rules = {
                **rules,
                **{
                    asset: {**rule, "lookback_months": lookback}
                    for asset, rule in enabled_rules.items()
                },
            }
            provider = TrendTarget(base, hedge, assets, varied_rules, trend["destination"])
            path = simulate_portfolio(
                values,
                base,
                rf,
                target_provider=provider,
                record_history=False,
                **panel.simulation_options,
            )
        points.append({"lookback_months": lookback, "gross": path["gross"], "net": path["net"]})
    return points


def analyze_asset_trend_sensitivity(
    panel,
    *,
    user_weights: dict[str, float],
    transaction_fee_rate: float,
    rebalancing_interval_months: int | None,
) -> dict:
    """Run a separate 100% allocation sweep for each enabled original asset."""
    values, _, hedge, rf, assets = _control_inputs(panel, user_weights)
    trend = panel.control_config["trend"]
    rules = _enabled_trend_rules(trend["asset_rules"], user_weights)
    if not rules:
        raise AnalyticsError("No positive-weight assets with SMA or Momentum enabled.")
    settings = {
        "transaction_fee_rate": transaction_fee_rate,
        "rebalancing_interval_months": rebalancing_interval_months,
        "record_history": False,
    }
    return {
        "by_asset": {
            asset: {
                "asset_id": asset,
                "indicator": rules[asset]["indicator"],
                "points": _single_asset_sensitivity(
                    values,
                    hedge,
                    rf,
                    assets,
                    asset,
                    rules[asset]["indicator"],
                    trend["destination"],
                    settings,
                ),
            }
            for asset in assets
            if asset in rules
        },
        "reproducibility": panel_provenance(panel),
    }


def _single_asset_sensitivity(
    values, hedge, rf, assets, asset_id, indicator, destination, settings
):
    base = np.array([float(asset == asset_id) for asset in assets])
    included = (base > 0) | (hedge > 0)
    values, base, hedge = values[:, included], base[included], hedge[included]
    assets = [asset for asset, keep in zip(assets, included, strict=True) if keep]
    points = []
    for lookback in TREND_LOOKBACKS:
        provider = TrendTarget(
            base,
            hedge,
            assets,
            {asset_id: {"indicator": indicator, "lookback_months": lookback}},
            destination,
        )
        path = simulate_portfolio(
            values,
            base,
            rf,
            target_provider=provider,
            **settings,
        )
        points.append(
            {
                "lookback_months": lookback,
                **{
                    side: {
                        metric: path[side][metric]
                        for metric in ("annualized_cagr", "maximum_drawdown")
                    }
                    for side in ("gross", "net")
                },
            }
        )
    return points


def attach_control_results(panel, result, user_weights):
    if panel.control_config.get("mode", "none") == "none":
        return
    controls = result.get("portfolio_controls")
    if controls is None:
        controls = compare_controls(panel, user_weights)
        result["portfolio_controls"] = controls
    controls["months"] = [str(m) for m in panel.returns.index]
    controls["source_metadata"] = panel.control_metadata
    if (
        panel.control_config["mode"] == "trend"
        and result.get("methodology") != "stationary_bootstrap"
        and controls.get("sensitivity", {}).get("scope") != "common_lookback"
    ):
        points = _trend_sensitivity(panel, user_weights, controls["controlled"])
        if points:
            controls["sensitivity"] = {"scope": "common_lookback", "points": points}
    if "folds" in result:
        for fold in result["folds"]:
            period = fold["validation"]
            indices = np.flatnonzero(
                (panel.returns.index >= period["start"]) & (panel.returns.index <= period["end"])
            )
            if "portfolio_controls" not in fold:
                fold["portfolio_controls"] = compare_controls(panel, user_weights, indices)
