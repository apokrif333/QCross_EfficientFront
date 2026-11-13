"""Sequential user-sleeve controls, independent of optimizer moments."""

from copy import deepcopy

import numpy as np

from app.analytics.models import AnalyticsError
from app.analytics.portfolio_simulator import simulate_portfolio


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
                    peak = window.max()
                    active[i] = np.exp(current - peak) >= np.exp(window - peak).mean()
            signals[asset] = {
                "active": bool(active[i]),
                "warmup": indicator != "disabled" and not ready,
            }
        kept = self.base * active
        mass = float(kept.sum())
        if self.destination == "remaining":
            desired = kept / mass if mass > 0 else self.hedge.copy()
            hedge_fraction = 0.0 if mass > 0 else 1.0
        else:
            hedge_fraction = max(0.0, 1 - mass)
            desired = kept + hedge_fraction * self.hedge
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


def compare_controls(panel, user_weights, indices=None, *, record_history=True, policy=None):
    if panel.control_config.get("mode", "none") == "none":
        return None
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
    settings = {**panel.simulation_options, "record_history": record_history, "policy": policy}
    if panel.control_config["mode"] == "downside_vol":
        params = panel.control_config["downside_vol"]
        provider = DownsideTarget(base, hedge, params["target_annual"], params["lookback_months"])
    else:
        params = panel.control_config["trend"]
        provider = TrendTarget(
            base, hedge, list(frame.columns), params["asset_rules"], params["destination"]
        )
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
        controlled = {k: controlled[k] for k in keys}
    return {
        "assets": list(frame.columns),
        "config": panel.control_config,
        "baseline": baseline,
        "controlled": controlled,
        "interpretation": "Exposure scaling is not a guaranteed realized risk cap.",
    }


def attach_control_results(panel, result, user_weights):
    if panel.control_config.get("mode", "none") == "none":
        return
    result["portfolio_controls"] = compare_controls(panel, user_weights)
    result["portfolio_controls"]["months"] = [str(m) for m in panel.returns.index]
    result["portfolio_controls"]["source_metadata"] = panel.control_metadata
    config = panel.control_config
    if config["mode"] == "trend":
        trend = config["trend"]
        asset = trend.get("sensitivity_asset")
        if asset and trend["asset_rules"].get(asset, {}).get("indicator", "disabled") != "disabled":
            from dataclasses import replace

            points = []
            for lookback in range(2, 37):
                changed = deepcopy(config)
                changed["trend"]["asset_rules"][asset]["lookback_months"] = lookback
                sample = compare_controls(
                    replace(panel, control_config=changed), user_weights, record_history=False
                )
                points.append({"lookback_months": lookback, **sample["controlled"]})
            result["portfolio_controls"]["sensitivity"] = {"asset": asset, "points": points}
    if "folds" in result:
        for fold in result["folds"]:
            period = fold["validation"]
            indices = np.flatnonzero(
                (panel.returns.index >= period["start"]) & (panel.returns.index <= period["end"])
            )
            if "portfolio_controls" not in fold:
                fold["portfolio_controls"] = compare_controls(panel, user_weights, indices)
