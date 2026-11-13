"""Sequential user-sleeve controls, independent of optimizer moments."""

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
    params = panel.control_config["downside_vol"]
    provider = DownsideTarget(base, hedge, params["target_annual"], params["lookback_months"])
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
    if "folds" in result:
        for fold in result["folds"]:
            period = fold["validation"]
            indices = np.flatnonzero(
                (panel.returns.index >= period["start"]) & (panel.returns.index <= period["end"])
            )
            fold["portfolio_controls"] = compare_controls(panel, user_weights, indices)
