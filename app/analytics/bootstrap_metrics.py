"""Fixed-allocation path statistics on the already sampled monthly panel."""

import numpy as np

PORTFOLIO_METRICS = (
    "annualized_cagr",
    "annualized_arithmetic_return",
    "annualized_volatility",
    "maximum_drawdown",
    "historical_sharpe_ratio",
    "terminal_wealth",
)


def fixed_portfolio_metrics(values, weights, risk_free_returns, **simulation_options) -> dict:
    from app.analytics.portfolio_simulator import simulate_portfolio

    simulation = simulate_portfolio(
        values,
        weights,
        risk_free_returns,
        record_history=False,
        allow_solver_residuals=True,
        **simulation_options,
    )
    return {
        **simulation["net"],
        "gross_metrics": simulation["gross"],
        "trading_costs": {
            k: simulation[k]
            for k in (
                "total_commissions",
                "trade_count",
                "rebalancing_events",
                "annualized_turnover",
                "execution_target_adjustment",
            )
        },
    }


def summarize_bootstrap_portfolio(records: list[dict], months: int, *, include_gross=True) -> dict:
    """Summarize each metric independently, including samples whose frontier failed."""
    distributions = {}
    for key in (*PORTFOLIO_METRICS, "return_shortfall", "excess_volatility"):
        distance = key in {"return_shortfall", "excess_volatility"}
        candidates = []
        for record in records:
            if distance:
                value = record.get(key) if record.get("status") == "optimal" else None
            else:
                value = record.get("portfolio_metrics", {}).get(key)
            candidates.append(value)
        values = np.asarray([v for v in candidates if v is not None and np.isfinite(v)])
        summary = dict.fromkeys(("mean", "median", "p5", "p95", "minimum", "maximum"))
        if len(values):
            summary.update(
                mean=float(values.mean()),
                median=float(np.median(values)),
                p5=float(np.quantile(values, 0.05)),
                p95=float(np.quantile(values, 0.95)),
                minimum=float(values.min()),
                maximum=float(values.max()),
            )
        distributions[key] = {
            **summary,
            "valid_count": len(values),
            "unavailable_count": len(records) - len(values),
            "horizon_months": months,
            "horizon_years": months / 12,
            "annualized": key.startswith("annualized_")
            or distance
            or key == "historical_sharpe_ratio",
            "units": "wealth_from_one_usd"
            if key == "terminal_wealth"
            else "dimensionless"
            if key == "historical_sharpe_ratio"
            else "decimal_fraction",
        }
    output = {
        "horizon": {
            "months": months,
            "years": months / 12,
            "initial_wealth": 1.0,
            "sample_length_policy": "full_selected_return_panel",
            "rebalancing": "request_simulation_settings_default_annual",
        },
        "metric_conventions": {
            "annualized_volatility": "realized_monthly_sample_std_ddof_1_times_sqrt_12",
            "historical_sharpe_ratio": "sample_std_ddof_1_of_aligned_monthly_differential_returns",
            "terminal_wealth": "selected_return_basis_units_from_one_initial_usd",
            "unavailable_values": "null_when_sharpe_undefined_or_numeric_overflow",
        },
        "distribution_interpretation": (
            "Historical resampled scenarios over the stated horizon. Percentiles are not "
            "calibrated probabilities of future returns. Path metrics use all available samples; "
            "efficiency distances use successful comparable frontier evaluations."
        ),
        "distributions": distributions,
    }
    if include_gross:
        gross = [
            {"portfolio_metrics": r.get("portfolio_metrics", {}).get("gross_metrics", {})}
            for r in records
        ]
        output["gross_distributions"] = {
            k: v
            for k, v in summarize_bootstrap_portfolio(gross, months, include_gross=False)[
                "distributions"
            ].items()
            if k in PORTFOLIO_METRICS
        }
    return output
