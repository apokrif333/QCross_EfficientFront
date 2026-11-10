import numpy as np
import pytest

from app.analytics.estimators import (
    estimate_covariance,
    gaussian_nll,
    linear_covariance,
    select_shrinkage,
    validate_covariance,
)
from app.analytics.models import AnalyticsError
from app.analytics.splitting import one_sided_folds


@pytest.mark.parametrize("t,k", [(90, 2), (91, 2), (120, 4), (181, 6), (600, 20)])
@pytest.mark.parametrize("target", ["identity", "diagonal"])
def test_covariance_cv_nll_weighting_and_determinism(t, k, target):
    values = np.random.default_rng(42).normal(size=(t, 3)) * [0.03, 0.02, 0.01]
    first = select_shrinkage(values, k, target)
    assert first == select_shrinkage(values, k, target)
    splits = one_sided_folds(t, k, min_train=60, min_validation=30)
    for i in [0, 19, 73, 100]:
        total = 0
        for train, validation in splits:
            matrix, _ = validate_covariance(linear_covariance(values[train], i / 100, target))
            total += len(validation) * gaussian_nll(
                values[validation], values[train].mean(0), matrix
            )
        assert first["nll_scores"][i] == pytest.approx(total / sum(len(v) for _, v in splits))
    assert first["intensity"] == first["grid"][np.argmin(first["nll_scores"])]
    fitted = estimate_covariance(values, "cv_linear", folds=k, target=target)
    expected, _ = validate_covariance(linear_covariance(values, first["intensity"], target))
    np.testing.assert_array_equal(fitted.matrix, expected)
    assert first["validation_coverage_fraction"] == (
        2 / 3 if t == 90 else sum(len(v) for _, v in splits) / t
    )


@pytest.mark.parametrize("t,k", [(89, 2), (119, 4), (599, 20), (240, 5)])
def test_covariance_cv_rejects_infeasible_history(t, k):
    with pytest.raises(AnalyticsError):
        select_shrinkage(np.ones((t, 3)), k)
