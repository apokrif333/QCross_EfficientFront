"""Path statistics shared by Train, real Test intervals and Bootstrap draws."""

import numpy as np
from scipy.spatial.distance import cdist

GROUPS = ("returns", "risk", "temporal", "drawdowns", "cross_asset")
HORIZONS = (3, 6, 12, 24, 60, 120, 240)

# Group, minimum numeric scale, primary score component.
METRICS = {
    "compounded_return": ("returns", 0.01, True),
    "cagr": ("returns", 0.01, False),
    "arithmetic_return": ("returns", 0.01, False),
    "volatility": ("risk", 0.01, True),
    "downside_volatility": ("risk", 0.01, True),
    "negative_frequency": ("risk", 0.05, True),
    "monthly_p05": ("risk", 0.005, True),
    "skewness": ("risk", 0.1, True),
    "excess_kurtosis": ("risk", 0.1, True),
    "max_drawdown": ("drawdowns", 0.01, True),
    "drawdown_duration": ("drawdowns", 1.0, True),
    "time_under_water": ("drawdowns", 0.05, True),
    "recovery_duration": ("drawdowns", 1.0, False),
    "recovery_censored": ("drawdowns", 0.05, False),
    "positive_streak": ("drawdowns", 1.0, True),
    "negative_streak": ("drawdowns", 1.0, True),
    "trend_duration": ("drawdowns", 1.0, True),
    "monthly_correlation": ("cross_asset", 0.05, True),
    "three_month_correlation": ("cross_asset", 0.05, True),
    "absolute_correlation": ("cross_asset", 0.05, True),
    "joint_negative_frequency": ("cross_asset", 0.05, True),
    "stress_joint_negative_frequency": ("cross_asset", 0.05, True),
}

ACF_FAMILIES = ("returns", "absolute", "squared", "sign")
METRICS.update(
    {
        f"acf_{family}_{lag}": ("temporal", 0.05, True)
        for family in ACF_FAMILIES
        for lag in range(1, 13)
    }
)


def _divide(numerator, denominator):
    return np.divide(
        numerator,
        denominator,
        out=np.full_like(numerator, np.nan, dtype=float),
        where=denominator > 0,
    )


def _lag_correlation(values, lag):
    # Contiguous time rows keep the repeated per-lag reductions efficient.
    left, right = values[..., :-lag], values[..., lag:]
    a = left - left.mean(axis=-1, keepdims=True)
    b = right - right.mean(axis=-1, keepdims=True)
    norm = np.sqrt(np.einsum("bat,bat->ba", a, a) * np.einsum("bat,bat->ba", b, b))
    return _divide(
        np.einsum("bat,bat->ba", a, b),
        np.where((np.ptp(left, axis=-1) > 0) & (np.ptp(right, axis=-1) > 0), norm, 0),
    )


def _pair_correlation(values, left, right):
    centered = values - values.mean(axis=1, keepdims=True)
    norm = np.sqrt(np.einsum("bta,bta->ba", centered, centered))
    norm[np.ptp(values, axis=1) == 0] = 0
    products = np.swapaxes(centered, 1, 2) @ centered
    return _divide(products[:, left, right], norm[:, left] * norm[:, right])


def _longest_run(mask):
    month = np.arange(1, mask.shape[1] + 1)[None, :, None]
    last_break = np.maximum.accumulate(np.where(mask, 0, month), axis=1)
    return (month - last_break).max(axis=1)


def standardization(train):
    """A fixed Train-only centering and a minimum monthly scale of 1 bp."""
    absolute = abs(train)
    return (
        train.mean(0),
        np.maximum(train.std(0), 0.0001),
        absolute.mean(0),
        np.maximum(absolute.std(0), 0.0001),
    )


def path_metrics(values, center, scale):
    """Array shape (paths, months, assets); NaN denotes an undefined statistic."""
    _, months, assets = values.shape
    positive, negative = values > 0, values < 0
    with np.errstate(divide="ignore", over="ignore"):
        logs = np.log1p(values)
        growth = np.cumsum(logs, axis=1)
        peaks = np.maximum(0, np.maximum.accumulate(growth, axis=1))
        drawdowns = -np.expm1(growth - peaks)
        compounded = np.expm1(growth[:, -1])
        cagr = np.expm1(growth[:, -1] * (12 / months))

    underwater = drawdowns > 1e-12
    trough = drawdowns.argmax(axis=1)
    positions = np.arange(months)[None, :, None]
    recovery = np.min(
        np.where((positions > trough[:, None, :]) & ~underwater, positions, months), axis=1
    )
    has_drawdown = underwater.any(axis=1)
    left, right = np.triu_indices(assets, 1)
    negative_float = negative.astype(float)
    joint_negative = np.swapaxes(negative_float, 1, 2) @ negative_float
    score = {
        "compounded_return": compounded,
        "cagr": cagr,
        "arithmetic_return": values.mean(axis=1) * 12,
        "volatility": values.std(axis=1, ddof=1) * np.sqrt(12),
        "downside_volatility": np.sqrt(12 * (np.minimum(values, 0) ** 2).mean(axis=1)),
        "negative_frequency": negative.mean(axis=1),
        "max_drawdown": drawdowns.max(axis=1),
        "drawdown_duration": _longest_run(underwater),
        "time_under_water": underwater.mean(axis=1),
        "recovery_duration": np.where(has_drawdown, np.minimum(recovery, months - 1) - trough, 0),
        "recovery_censored": (has_drawdown & (recovery == months)).astype(float),
        "positive_streak": _longest_run(positive),
        "negative_streak": _longest_run(negative),
        "joint_negative_frequency": joint_negative[:, left, right] / months,
    }
    if months >= 6:
        score["monthly_correlation"] = _pair_correlation(values, left, right)
        score["absolute_correlation"] = _pair_correlation(abs(values), left, right)
        # A trend is a run of positive or negative trailing three-month total returns.
        trend = logs[:, :-2] + logs[:, 1:-1] + logs[:, 2:]
        score["trend_duration"] = np.maximum(_longest_run(trend > 0), _longest_run(trend < 0))
        stress = ((values - center) / scale).mean(axis=2) < -1
        stress_count = stress.sum(axis=1, keepdims=True)
        stressed_losses = negative_float * stress[:, :, None]
        joint_stress = np.swapaxes(stressed_losses, 1, 2) @ stressed_losses
        score["stress_joint_negative_frequency"] = _divide(
            joint_stress[:, left, right], np.where(stress_count >= 3, stress_count, 0)
        )
    if months >= 12:
        centered = values - values.mean(axis=1, keepdims=True)
        variance = np.where(np.ptp(values, axis=1) > 0, (centered**2).mean(axis=1), 0)
        score["monthly_p05"] = np.quantile(values, 0.05, axis=1)
        score["skewness"] = _divide((centered**3).mean(axis=1), variance**1.5)
        if months >= 24:
            score["excess_kurtosis"] = _divide((centered**4).mean(axis=1), variance**2) - 3
    if months >= 18:
        blocks = months // 3
        three_month = np.prod(1 + values[:, : blocks * 3].reshape(-1, blocks, 3, assets), axis=2)
        score["three_month_correlation"] = _pair_correlation(three_month, left, right)
    if months >= 9:
        contiguous_time = np.ascontiguousarray(values.swapaxes(1, 2))
        series = (
            contiguous_time,
            abs(contiguous_time),
            contiguous_time**2,
            np.sign(contiguous_time),
        )
        for family, data in zip(ACF_FAMILIES, series, strict=True):
            for lag in range(1, min(12, months - 8) + 1):
                score[f"acf_{family}_{lag}"] = _lag_correlation(data, lag)
    return score


def ensemble_crps(samples, observations):
    """CRPS and empirical mid-rank for every observation and column."""
    sorted_samples = np.sort(samples, axis=0)
    n = len(samples)
    spread = np.arange(1 - n, n, 2) @ sorted_samples / n**2
    return _sorted_crps(sorted_samples, observations, spread)


def _sorted_crps(sorted_samples, observations, spread=None):
    n = len(sorted_samples)
    if spread is None:
        spread = np.arange(1 - n, n, 2) @ sorted_samples / n**2
    prefix = np.vstack((np.zeros((1, sorted_samples.shape[1])), sorted_samples.cumsum(0)))
    crps = np.empty_like(observations, dtype=float)
    rank = np.empty_like(crps)
    for column, sample in enumerate(sorted_samples.T):
        actual = observations[:, column]
        left = np.searchsorted(sample, actual)
        right = np.searchsorted(sample, actual, side="right")
        distance = (prefix[-1, column] - 2 * prefix[left, column] + actual * (2 * left - n)) / n
        crps[:, column] = np.maximum(0, distance - spread[column])
        rank[:, column] = (left + right) / (2 * n)
    invalid = ~np.isfinite(observations)
    crps[invalid] = np.nan
    rank[invalid] = np.nan
    return crps, rank


def energy_score(samples, observations, batch=128):
    """Exact empirical energy score with bounded pairwise distance arrays."""
    distance = sum(
        cdist(samples[i : i + batch], samples).sum() for i in range(0, len(samples), batch)
    )
    score = cdist(observations, samples).mean(axis=1) - distance / (2 * len(samples) ** 2)
    return np.maximum(0, score)


def variogram_components(values, train_scale):
    """Yield arrays of |x_a-x_b|^0.5, grouped by lag or by cross-asset pair."""
    center, scale, abs_center, abs_scale = train_scale
    signed = (values - center) / scale
    absolute = (abs(values) - abs_center) / abs_scale
    for name, data in (("temporal_returns", signed), ("temporal_absolute", absolute)):
        for lag in range(1, min(12, values.shape[1] - 1) + 1):
            yield (name, lag), np.sqrt(abs(data[:, lag:] - data[:, :-lag]))
    left, right = np.triu_indices(values.shape[2], 1)
    for start in range(0, len(left), 16):
        pair = slice(start, start + 16)
        yield (
            ("cross_asset", start),
            np.sqrt(abs(signed[:, :, left[pair]] - signed[:, :, right[pair]])),
        )


def variogram_scores(forecast_means, observed_components):
    sums, counts = {}, {}
    for key, actual in observed_components:
        name, _ = key
        squared = (actual - forecast_means[key]) ** 2
        # Each lag has equal weight. Cross-asset chunks are weighted by pair count.
        weight = squared.shape[-1] if name == "cross_asset" else 1
        sums[name] = sums.get(name, 0) + squared.mean(axis=(1, 2)) * weight
        counts[name] = counts.get(name, 0) + weight
    return {name: values / counts[name] for name, values in sums.items()}
