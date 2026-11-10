"""Deterministic bidirectional one-sided block CV on chronologically ordered rows.

Equal-length sides choose the earlier (before-validation) interval. There is no
purge or embargo. Separate folds may reuse training observations.
"""

from numbers import Integral

import numpy as np

from app.analytics.models import AnalyticsError

CV_METHODOLOGY = "bidirectional_one_sided_block_kfold"
SUPPORTED_FOLDS = tuple(range(2, 21, 2))
MIN_PORTFOLIO_TRAIN = 120
MIN_PORTFOLIO_VALIDATION = 60
MIN_COVARIANCE_TRAIN = 60
MIN_COVARIANCE_VALIDATION = 30


def _valid_integer(value: int) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool)


def _check_configuration(folds: int, min_train: int, min_validation: int) -> None:
    if not _valid_integer(folds) or folds not in SUPPORTED_FOLDS:
        raise AnalyticsError(
            f"Requested K={folds}; supported K values are even integers 2 through 20 (default 4)."
        )
    if any(not _valid_integer(v) or v < 1 for v in (min_train, min_validation)):
        raise AnalyticsError("Minimum train and validation sizes must be positive integers.")


def _bounds(observations: int, folds: int, min_train: int) -> list[tuple[int, int]]:
    if folds == 2:
        # Largest equal outer windows when a complete two-block partition cannot
        # leave min_train rows on both opposite sides. Never overlap validation.
        if observations // 2 < min_train:
            size = min(observations // 2, observations - min_train)
            return [(0, size), (observations - size, observations)]
    size, remainder = divmod(observations, folds)
    lengths = [size + (i < remainder) for i in range(folds)]
    ends = np.cumsum([0, *lengths]).tolist()
    return list(zip(ends[:-1], ends[1:], strict=True))


def _feasible(observations: int, folds: int, min_train: int, min_validation: int) -> bool:
    return all(
        end - start >= min_validation and max(start, observations - end) >= min_train
        for start, end in _bounds(observations, folds, min_train)
    )


def minimum_observations(folds: int = 4, *, min_train: int, min_validation: int) -> int:
    """Exact minimum, including array_split rounding and configurable limits."""
    _check_configuration(folds, min_train, min_validation)
    low, high = 0, folds * (min_train + min_validation)
    while low < high:
        middle = (low + high) // 2
        if _feasible(middle, folds, min_train, min_validation):
            high = middle
        else:
            low = middle + 1
    return low


def eligible_folds(observations: int, *, min_train: int, min_validation: int) -> list[int]:
    return [
        k
        for k in SUPPORTED_FOLDS
        if observations
        >= minimum_observations(k, min_train=min_train, min_validation=min_validation)
    ]


def one_sided_folds(
    observations: int, folds: int = 4, *, min_train: int, min_validation: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return increasing (train, validation) indices; input rows must be chronological."""
    _check_configuration(folds, min_train, min_validation)
    if not _valid_integer(observations) or observations < 0:
        raise AnalyticsError("Observation count must be a nonnegative integer.")
    required = minimum_observations(folds, min_train=min_train, min_validation=min_validation)
    if observations < required:
        eligible = eligible_folds(observations, min_train=min_train, min_validation=min_validation)
        raise AnalyticsError(
            f"Requested K={folds}, actual observations={observations}, "
            f"required minimum={required}; "
            f"minimum train size={min_train}, minimum validation size={min_validation} per fold. "
            f"Eligible K values: {eligible}. Select a longer history or an eligible K."
        )
    result = []
    for start, end in _bounds(observations, folds, min_train):
        training = np.arange(start) if start >= observations - end else np.arange(end, observations)
        result.append((training, np.arange(start, end)))
    return result


def fold_direction(training: np.ndarray, validation: np.ndarray) -> str:
    return "train_before_validation" if training[-1] < validation[0] else "train_after_validation"


def validation_metadata(observations: int, splits: list) -> dict:
    validated = np.concatenate([validation for _, validation in splits])
    uncovered = np.setdiff1d(np.arange(observations), validated)
    return {
        "total_observations": observations,
        "validation_observations": len(validated),
        "validation_coverage_fraction": len(validated) / observations,
        "unvalidated_indices": uncovered.tolist(),
    }


def validate_cv_configuration(
    observations: int, folds: int, *, portfolio: bool, covariance_method: str
) -> list:
    """Operation-aware preflight, also used by numerical jobs without FastAPI."""
    outer = (
        one_sided_folds(
            observations,
            folds,
            min_train=MIN_PORTFOLIO_TRAIN,
            min_validation=MIN_PORTFOLIO_VALIDATION,
        )
        if portfolio
        else []
    )
    if covariance_method == "cv_linear":
        one_sided_folds(
            observations,
            folds,
            min_train=MIN_COVARIANCE_TRAIN,
            min_validation=MIN_COVARIANCE_VALIDATION,
        )
        for number, (training, _) in enumerate(outer, 1):
            try:
                one_sided_folds(
                    len(training),
                    folds,
                    min_train=MIN_COVARIANCE_TRAIN,
                    min_validation=MIN_COVARIANCE_VALIDATION,
                )
            except AnalyticsError as exc:
                raise AnalyticsError(f"Inner covariance CV in outer fold {number}: {exc}") from exc
    return outer
