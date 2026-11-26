"""Fixed user allocation versus training-only choices on paired validation samples."""

from base64 import b64encode

import numpy as np

from app.analytics.bootstrap_metrics import fixed_portfolio_metrics
from app.analytics.optimizer import PortfolioOptimizer

METRICS = {
    "arithmetic_return": "annualized_arithmetic_return",
    "cagr": "annualized_cagr",
    "volatility": "annualized_volatility",
    "sharpe": "historical_sharpe_ratio",
    "max_drawdown": "maximum_drawdown",
}
JOINED_METRICS = {
    **METRICS,
    "calmar": "calmar_ratio",
    "sortino": "sortino_ratio",
    "terminal_wealth": "terminal_wealth",
    "commissions": "total_commissions",
    "annualized_commissions": "annualized_commissions",
    "capital_gains_tax": "total_capital_gains_tax",
    "annualized_capital_gains_tax": "annualized_capital_gains_tax",
    "total_costs": "total_costs",
    "annualized_total_costs": "annualized_total_costs",
    "turnover": "annualized_turnover",
}


def validation_metrics(values, weights, risk_free, **simulation_options):
    metrics = fixed_portfolio_metrics(values, weights, risk_free, **simulation_options)
    return metrics


def _encode_wealth(wealth):
    # Float32 natural logs keep full monthly series compact and retain relative precision.
    values = np.asarray(wealth, dtype=float)
    logs = np.full(values.shape, np.nan)
    np.log(values, out=logs, where=(values > 0) & np.isfinite(values))
    logs[values == 0] = -np.inf
    return b64encode(logs.astype("<f4").tobytes()).decode("ascii")


def compare_validation(
    values,
    risk_free,
    user,
    trained,
    assets,
    *,
    user_metrics=None,
    include_wealth=False,
    **simulation_options,
):
    record = {
        "status": trained.status,
        "diagnostics": trained.diagnostics,
        "user": user_metrics
        if user_metrics is not None
        else validation_metrics(
            values, user, risk_free, include_wealth=include_wealth, **simulation_options
        ),
        "optimized": None,
        "weights": None,
        "differences": {},
    }
    if include_wealth:
        user_wealth = record["user"].pop("net_wealth", None)
        if user_wealth is not None:
            record["wealth_log_f32"] = {"user": _encode_wealth(user_wealth)}
    if trained.weights is None:
        return record
    record["weights"] = dict(zip(assets, trained.weights.tolist(), strict=True))
    record["optimized"] = validation_metrics(
        values, trained.weights, risk_free, include_wealth=include_wealth, **simulation_options
    )
    if include_wealth:
        record.setdefault("wealth_log_f32", {})["optimized"] = _encode_wealth(
            record["optimized"].pop("net_wealth")
        )
    record["differences"] = {
        f"delta_{name}": None if a is None or b is None else a - b
        for name, key in JOINED_METRICS.items()
        for a, b in [(_metric(record["optimized"], key), _metric(record["user"], key))]
    }
    return record


def _metric(path, key):
    return path.get(key, path.get("trading_costs", {}).get(key))


def summarize(records, *, metrics=METRICS):
    valid = [r for r in records if r.get("optimized") is not None]
    stats = {}
    for name in metrics:
        key = f"delta_{name}"
        data = np.array(
            [r["differences"][key] for r in valid if r["differences"].get(key) is not None]
        )
        stats[key] = (
            {
                "mean": float(data.mean()),
                "median": float(np.median(data)),
                "standard_deviation": float(data.std()),
                "p5": float(np.quantile(data, 0.05)),
                "p95": float(np.quantile(data, 0.95)),
                "minimum": float(data.min()),
                "maximum": float(data.max()),
                "valid_count": len(data),
            }
            if len(data)
            else None
        )
    wins = {}
    for label, keys, condition in (
        ("higher_cagr", ["delta_cagr"], lambda d: d["delta_cagr"] > 0),
        ("higher_sharpe", ["delta_sharpe"], lambda d: d["delta_sharpe"] > 0),
        ("lower_volatility", ["delta_volatility"], lambda d: d["delta_volatility"] < 0),
        ("lower_drawdown", ["delta_max_drawdown"], lambda d: d["delta_max_drawdown"] < 0),
        (
            "lower_annualized_total_costs",
            ["delta_annualized_total_costs"],
            lambda d: d["delta_annualized_total_costs"] < 0,
        ),
        (
            "higher_cagr_no_higher_volatility",
            ["delta_cagr", "delta_volatility"],
            lambda d: d["delta_cagr"] > 0 and d["delta_volatility"] <= 0,
        ),
    ):
        eligible = [
            r["differences"]
            for r in valid
            if all(r["differences"].get(k) is not None for k in keys)
        ]
        wins[label] = {
            "fraction": float(np.mean([condition(d) for d in eligible])) if eligible else None,
            "denominator": len(eligible),
        }
    return {
        "valid_count": len(valid),
        "failed_count": len(records) - len(valid),
        "requested_comparisons": len(records),
        "differences": stats,
        "win_frequencies": wins,
        "drawdown_convention": "positive_loss_magnitude; negative_delta_is_improvement",
        "results": records,
    }


def joined_validation_comparison(
    iteration, values, assets, user, optimized, validation_hash, *, user_failure=None
):
    mean = values.mean(0) * 12
    covariance = np.cov(values, rowvar=False, ddof=1) * 12
    record = {
        "pair": iteration + 1,
        "validation_generation": iteration + 1,
        "validation_sha256": validation_hash,
        "status": optimized["status"] if user is not None else "control_failed",
        "diagnostics": {"reason": user_failure or optimized.get("reason", "User simulation failed")}
        if optimized["status"] != "optimal" or user is None
        else {},
        "fold_weights": [fold["weights"] for fold in optimized["folds"]],
        "user": None,
        "optimized": None,
        "differences": {},
    }
    optimized_metrics = optimized["metrics"]
    if optimized_metrics is not None:
        wealth = optimized_metrics.pop("net_wealth", None)
        if user is not None and wealth is not None:
            record["wealth_log_f32"] = {"optimized": _encode_wealth(wealth)}
    if user is None:
        return record, None, mean, covariance
    if "net_wealth" in user:
        record.setdefault("wealth_log_f32", {})["user"] = _encode_wealth(user["net_wealth"])

    def scalar_metrics(path):
        return {
            **{key: _metric(path, key) for key in JOINED_METRICS.values()},
            "unpaid_capital_gains_tax": _metric(path, "unpaid_capital_gains_tax"),
        }

    def point(path):
        return {
            "expected_return": path["annualized_arithmetic_return"],
            "volatility": path["annualized_volatility"],
        }

    record["user"] = scalar_metrics(user)
    record["tax_insolvent"] = {
        "user": user.get("tax_insolvent"),
        "optimized": optimized_metrics.get("tax_insolvent") if optimized_metrics else None,
    }
    if optimized_metrics is not None:
        record["optimized"] = scalar_metrics(optimized_metrics)
        for name, metric in JOINED_METRICS.items():
            a, b = record["optimized"][metric], record["user"][metric]
            record["differences"][f"delta_{name}"] = (
                a - b if a is not None and b is not None else None
            )
    cloud = {
        "pair": iteration + 1,
        "assets": {
            asset: {
                "expected_return": float(mean[i]),
                "volatility": float(np.sqrt(max(0, covariance[i, i]))),
            }
            for i, asset in enumerate(assets)
        },
        "user": point(user),
        "optimized": point(optimized_metrics) if optimized_metrics is not None else None,
    }
    return record, cloud, mean, covariance


def validation_frontier(mean, covariance, constraints):
    points = []
    try:
        engine = PortfolioOptimizer(mean, covariance, constraints)
        low, high = engine.gmv(), engine.maximum_return()
        if low.weights is not None and high.weights is not None:
            for target in np.linspace(mean @ low.weights, mean @ high.weights, 51):
                fit = engine.target_return(float(target))
                if fit.weights is not None:
                    points.append(
                        {
                            "expected_return": float(mean @ fit.weights),
                            "volatility": float(
                                np.sqrt(max(0, fit.weights @ covariance @ fit.weights))
                            ),
                        }
                    )
    except Exception as exc:  # Ex-post diagnostics are optional.
        return {
            "validation_frontier": [],
            "frontier_diagnostics": {"status": "failed", "reason": str(exc)},
        }
    return {
        "validation_frontier": points,
        "annual_mean": mean.tolist(),
        "annual_covariance": covariance.tolist(),
    }
