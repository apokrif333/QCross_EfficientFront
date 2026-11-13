"""Historical in-sample comparison of shared-simulator policies."""

import time

import numpy as np

from app.analytics.constraints import build_constraints, user_allocation
from app.analytics.models import AnalyticsError
from app.analytics.portfolio_simulator import CalendarPolicy, ThresholdPolicy, simulate_portfolio
from app.analytics.provenance import panel_provenance, reproducible

CALENDARS = [
    (1, "Каждый месяц"),
    (3, "Каждый квартал"),
    (12, "Каждый год"),
    (24, "Каждые 2 года"),
    (36, "Каждые 3 года"),
    (48, "Каждые 4 года"),
    (60, "Каждые 5 лет"),
    (None, "Buy & Hold"),
]
THRESHOLDS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 150, 200]


@reproducible
def analyze_rebalancing(
    panel,
    *,
    user_weights,
    transaction_fee_rate=0.0,
    risk_free_rate=0.03,
    asset_constraints=None,
    group_constraints=None,
    **options,
):
    started = time.perf_counter()
    if panel.risk_free_source == "historical_bil" and panel.risk_free_returns is None:
        raise AnalyticsError("Historical BIL mode requires aligned monthly BIL observations.")
    weights = user_allocation(panel.assets, user_weights)
    if weights is None:
        raise AnalyticsError("Rebalancing optimization requires explicit user_weights.")
    constraints = build_constraints(
        panel.assets, asset_constraints, group_constraints, panel.groups
    )
    constraints.require_feasible(weights)
    rf = (
        panel.risk_free_returns
        if panel.risk_free_returns is not None
        else np.full(len(panel.values), (1 + risk_free_rate) ** (1 / 12) - 1)
    )
    months = [str(m) for m in panel.returns.index]
    strategies = []
    policies = [
        (f"calendar_{interval}", label, CalendarPolicy(interval), interval, None)
        for interval, label in CALENDARS
    ]
    policies += [
        (
            f"threshold_{threshold}",
            f"Порог {threshold}%",
            ThresholdPolicy(threshold / 100),
            None,
            threshold,
        )
        for threshold in THRESHOLDS
    ]
    for strategy_id, label, policy, interval, threshold in policies:
        result = simulate_portfolio(
            panel.values, weights, rf, policy=policy, transaction_fee_rate=transaction_fee_rate
        )
        for event in result["events"]:
            event["date"] = months[max(0, event["month_index"])]
            event["boundary"] = (
                "initial_allocation_before_returns"
                if event["month_index"] < 0
                else "month_end_after_returns"
            )
            event["triggering_assets"] = [panel.assets[i] for i in event["triggering_assets"]]
            event["trades"] = dict(zip(panel.assets, event["trades"], strict=True))
        strategies.append(
            {
                "id": strategy_id,
                "label": label,
                "calendar_months": interval,
                "threshold_percent": threshold,
                "annual_baseline": interval == 12 and threshold is None,
                **result,
            }
        )
    strategies.sort(
        key=lambda s: (s["net"]["annualized_cagr"] is None, -(s["net"]["annualized_cagr"] or 0))
    )
    return {
        "status": "complete",
        "assets": panel.assets,
        "currency": "USD",
        "return_basis": panel.return_basis,
        "risk_free_source": panel.risk_free_source,
        "risk_free_mean_return": float(rf.mean() * 12),
        "reference_metadata": panel.reference_metadata,
        "period": {"start": months[0], "end": months[-1], "observations": len(months)},
        "months": months,
        "user_weights": dict(zip(panel.assets, weights.tolist(), strict=True)),
        "transaction_fee_rate": transaction_fee_rate,
        "strategies": strategies,
        "methodology": "historical_in_sample_rebalancing_comparison_v1",
        "simulation_settings": {
            "transaction_fee_rate": transaction_fee_rate,
            "baseline_months": 12,
        },
        "source_metadata": panel.metadata,
        "warnings": panel.warnings,
        "covariance_method": options.get("covariance_method", "sample"),
        "covariance_parameters": {"used_for_simulation": False},
        "covariance_diagnostics": {},
        "differential_covariance_parameters": {},
        "reproducibility": panel_provenance(panel),
        "execution_seconds": time.perf_counter() - started,
    }
