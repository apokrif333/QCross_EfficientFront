"""Fixed-allocation path statistics on the already sampled monthly panel."""

import numpy as np

from app.analytics.models import AnalyticsError

PORTFOLIO_METRICS = (
    "annualized_cagr",
    "annualized_arithmetic_return",
    "annualized_volatility",
    "maximum_drawdown",
    "historical_sharpe_ratio",
    "terminal_wealth",
)


def fixed_portfolio_metrics(values, weights, risk_free_returns) -> dict:
    """Monthly rebalancing, historical volatility ddof=0, differential Sharpe ddof=1.

    Log wealth prevents overflow while calculating drawdown and CAGR. An
    unrepresentable terminal wealth/CAGR or undefined Sharpe is returned as null.
    """
    monthly = np.asarray(values, dtype=float) @ np.asarray(weights, dtype=float)
    risk_free = np.asarray(risk_free_returns, dtype=float)
    if (
        monthly.ndim != 1
        or len(monthly) < 2
        or risk_free.shape != monthly.shape
        or not np.isfinite(monthly).all()
        or not np.isfinite(risk_free).all()
        or np.any(monthly < -1)
    ):
        raise AnalyticsError(
            "Fixed portfolio metrics require aligned finite monthly returns >= -1."
        )
    with np.errstate(divide="ignore"):
        log_wealth = np.r_[0.0, np.cumsum(np.log1p(monthly))]
    peak = np.maximum.accumulate(log_wealth)
    drawdown = -np.expm1(log_wealth - peak)
    with np.errstate(over="ignore"):
        terminal = float(np.exp(log_wealth[-1]))
        cagr = float(np.expm1(log_wealth[-1] * 12 / len(monthly)))
    differential = monthly - risk_free
    deviation = differential.std(ddof=1)
    metrics = {
        "annualized_cagr": cagr if np.isfinite(cagr) else None,
        "annualized_arithmetic_return": float(monthly.mean() * 12),
        "annualized_volatility": float(monthly.std(ddof=0) * np.sqrt(12)),
        "maximum_drawdown": float(drawdown.max()),
        "historical_sharpe_ratio": float(np.sqrt(12) * differential.mean() / deviation)
        if deviation > 1e-12
        else None,
        "terminal_wealth": terminal if np.isfinite(terminal) else None,
    }
    return {
        key: value if value is None or np.isfinite(value) else None
        for key, value in metrics.items()
    }


def summarize_bootstrap_portfolio(records: list[dict], months: int) -> dict:
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
    return {
        "horizon": {
            "months": months,
            "years": months / 12,
            "initial_wealth": 1.0,
            "sample_length_policy": "full_selected_return_panel",
            "rebalancing": "monthly_fixed_weights",
        },
        "metric_conventions": {
            "annualized_volatility": "realized_monthly_population_std_ddof_0_times_sqrt_12",
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
