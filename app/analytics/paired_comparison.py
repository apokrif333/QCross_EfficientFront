"""Fixed user allocation versus training-only choices on paired validation samples."""

from base64 import b64encode

import numpy as np

from app.analytics.bootstrap_metrics import fixed_portfolio_metrics
from app.analytics.models import OptimizationResult
from app.analytics.optimizer import PortfolioOptimizer

METRICS = {
    "arithmetic_return": "annualized_arithmetic_return",
    "cagr": "annualized_cagr",
    "volatility": "annualized_volatility",
    "sharpe": "historical_sharpe_ratio",
    "max_drawdown": "maximum_drawdown",
}


def validation_metrics(values, weights, risk_free, **simulation_options):
    metrics = fixed_portfolio_metrics(values, weights, risk_free, **simulation_options)
    return metrics


def _encode_wealth(wealth):
    # Float32 natural logs keep full monthly series compact and retain relative precision.
    values = np.asarray(wealth, dtype=float)
    logs = np.full(values.shape, np.nan)
    np.log(values, out=logs, where=(values > 0) & np.isfinite(values))
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
        f"delta_{name}": record["optimized"][key] - record["user"][key]
        if record["optimized"][key] is not None and record["user"][key] is not None
        else None
        for name, key in METRICS.items()
    }
    return record


def summarize(records):
    valid = [r for r in records if r.get("optimized") is not None]
    stats = {}
    for name in METRICS:
        key = f"delta_{name}"
        data = np.array(
            [r["differences"][key] for r in valid if r["differences"].get(key) is not None]
        )
        stats[key] = (
            {
                "mean": float(data.mean()),
                "median": float(np.median(data)),
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


class PairedBootstrap:
    def __init__(self, assets, user, constraints, *, controls_enabled=False, **simulation_options):
        self.controls_enabled = controls_enabled
        self.simulation_options = simulation_options
        self.assets, self.user, self.constraints = assets, user, constraints
        self.records, self.clouds, self.means, self.covariances = [], [], [], []
        self.trained = OptimizationResult("train_failed", None, {})

    def consume(self, iteration, values, risk_free, estimate, *, control_sample=None):
        try:
            self._consume(iteration, values, risk_free, estimate, control_sample)
        except Exception as exc:
            if iteration % 2 == 0:
                self.trained = OptimizationResult("train_failed", None, {"reason": str(exc)})
            else:
                self.records.append(
                    {
                        "pair": iteration // 2 + 1,
                        "validation_generation": iteration + 1,
                        "status": "failed",
                        "diagnostics": {"reason": str(exc)},
                    }
                )

    def _consume(self, iteration, values, risk_free, estimate, control_sample):
        user_metrics = None
        if self.controls_enabled:
            if not control_sample or "controlled" not in control_sample:
                raise ValueError(
                    "Controlled portfolio unavailable: "
                    + (control_sample or {}).get("reason", "missing simulation")
                )
            path = control_sample["controlled"]
            wealth = path.pop("net_wealth", None)
            user_metrics = {
                **path["net"],
                **({"net_wealth": wealth} if wealth is not None else {}),
                "gross_metrics": path["gross"],
                "trading_costs": {
                    k: path[k]
                    for k in (
                        "total_commissions",
                        "trade_count",
                        "rebalancing_events",
                        "annualized_turnover",
                    )
                },
            }
        if iteration % 2 == 0:
            self.trained = OptimizationResult("train_failed", None, {})
            try:
                if estimate is not None:
                    self.trained = PortfolioOptimizer(
                        values.mean(0) * 12, estimate.matrix * 12, self.constraints
                    ).max_return_at_user_risk(
                        self.user,
                        training_volatility=user_metrics["annualized_volatility"]
                        if user_metrics is not None
                        else None,
                    )
            except Exception as exc:
                self.trained = OptimizationResult("not_comparable", None, {"reason": str(exc)})
            if estimate is not None:
                self.trained.diagnostics.update(
                    covariance_parameters=estimate.parameters,
                    covariance_diagnostics=estimate.diagnostics,
                )
            return
        mean, covariance = values.mean(0) * 12, np.cov(values, rowvar=False, ddof=1) * 12
        self.means.append(mean)
        self.covariances.append(covariance)
        record = compare_validation(
            values,
            risk_free,
            self.user,
            self.trained,
            self.assets,
            user_metrics=user_metrics,
            include_wealth=True,
            **self.simulation_options,
        )
        record.update(
            pair=iteration // 2 + 1,
            train_generation=iteration,
            validation_generation=iteration + 1,
            user_portfolio_mode="controlled" if self.controls_enabled else "passive",
        )
        self.clouds.append(
            {
                "pair": iteration // 2 + 1,
                "assets": {
                    a: {
                        "expected_return": float(mean[i]),
                        "volatility": float(np.sqrt(max(0, covariance[i, i]))),
                    }
                    for i, a in enumerate(self.assets)
                },
                "user": {
                    "expected_return": record["user"]["annualized_arithmetic_return"],
                    "volatility": record["user"]["annualized_volatility"],
                },
                "optimized": {
                    "expected_return": record["optimized"]["annualized_arithmetic_return"],
                    "volatility": record["optimized"]["annualized_volatility"],
                }
                if record["optimized"]
                else None,
            }
        )
        self.records.append(record)

    def result(self, observations):
        result = {
            **summarize(self.records),
            "user_weights": dict(zip(self.assets, self.user.tolist(), strict=True)),
            "analysis_version": "paired_validation_v3",
            "user_portfolio_mode": "controlled" if self.controls_enabled else "passive",
            "generated_datasets": len(self.records) * 2,
            "horizon_months": observations,
            "clouds": self.clouds,
            "interpretation": (
                "Conditional synthetic validation from the same empirical process; "
                "not an independent future market test."
            ),
        }
        points = []
        try:
            if self.means:
                mean, cov = np.mean(self.means, axis=0), np.mean(self.covariances, axis=0)
                engine = PortfolioOptimizer(mean, cov, self.constraints)
                low, high = engine.gmv(), engine.maximum_return()
                if low.weights is not None and high.weights is not None:
                    for target in np.linspace(mean @ low.weights, mean @ high.weights, 51):
                        fit = engine.target_return(float(target))
                        if fit.weights is not None:
                            points.append(
                                {
                                    "expected_return": float(mean @ fit.weights),
                                    "volatility": float(
                                        np.sqrt(max(0, fit.weights @ cov @ fit.weights))
                                    ),
                                }
                            )
                result.update(annual_mean=mean.tolist(), annual_covariance=cov.tolist())
        except Exception as exc:
            points = []
            result["frontier_diagnostics"] = {"status": "failed", "reason": str(exc)}
        result["validation_frontier"] = points
        return result
