import numpy as np


def historical_metrics(monthly: np.ndarray) -> dict:
    """Monthly-rebalanced simple returns. Include initial wealth in drawdown peaks."""
    monthly = np.asarray(monthly, dtype=float)
    wealth = np.concatenate(([1.0], np.cumprod(1 + monthly)))
    peak = np.maximum.accumulate(wealth)
    drawdown = np.divide(wealth, peak, out=np.zeros_like(wealth), where=peak > 0) - 1
    cagr = -1.0 if np.any(monthly == -1) else float(np.expm1(np.log1p(monthly).mean() * 12))
    return {"historical_cagr": cagr, "historical_max_drawdown": float(-drawdown.min())}


def portfolio_metrics(
    weights: np.ndarray,
    values: np.ndarray,
    mean: np.ndarray,
    covariance: np.ndarray,
    risk_free_rate: float = 0.03,
) -> dict:
    expected = float(weights @ mean)
    volatility = float(np.sqrt(max(0, weights @ covariance @ weights)))
    return {
        "expected_return": expected,
        "volatility": volatility,
        "sharpe_ratio": (expected - risk_free_rate) / volatility if volatility > 1e-10 else None,
        **historical_metrics(values @ weights),
    }


def correlation_diagnostics(values: np.ndarray, assets: list[str]) -> dict:
    sample = np.cov(values, rowvar=False, ddof=1)
    scales = np.sqrt(np.maximum(np.diag(sample), 0))
    divisor = np.outer(scales, scales)
    correlation = np.divide(sample, divisor, out=np.full_like(sample, np.nan), where=divisor > 0)
    warnings = [
        f"Correlation is undefined for zero-variance asset {assets[i]}."
        for i in range(len(assets))
        if scales[i] == 0
    ]
    for i in range(len(assets)):
        for j in range(i + 1, len(assets)):
            if abs(correlation[i, j]) > 0.98:
                warnings.append(f"Absolute correlation exceeds 0.98: {assets[i]}, {assets[j]}.")
    return {
        "matrix": [[float(x) if np.isfinite(x) else None for x in row] for row in correlation],
        "warnings": warnings,
    }
