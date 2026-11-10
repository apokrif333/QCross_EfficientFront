import numpy as np
import pytest

from app.analytics.models import AnalyticsError
from app.analytics.splitting import minimum_observations, one_sided_folds, validation_metadata


@pytest.mark.parametrize(
    "observations,k,train_min,val_min,validation_ranges",
    [
        (180, 2, 120, 60, [(0, 60), (120, 180)]),
        (200, 2, 120, 60, [(0, 80), (120, 200)]),
        (240, 2, 120, 60, [(0, 120), (120, 240)]),
        (241, 2, 120, 60, [(0, 121), (121, 241)]),
        (240, 4, 120, 60, [(0, 60), (60, 120), (120, 180), (180, 240)]),
        (241, 4, 120, 60, [(0, 61), (61, 121), (121, 181), (181, 241)]),
        (10000, 4, 120, 60, [(0, 2500), (2500, 5000), (5000, 7500), (7500, 10000)]),
        (360, 6, 120, 60, [(i * 60, (i + 1) * 60) for i in range(6)]),
        (1200, 20, 120, 60, [(i * 60, (i + 1) * 60) for i in range(20)]),
        (90, 2, 60, 30, [(0, 30), (60, 90)]),
        (120, 4, 60, 30, [(i * 30, (i + 1) * 30) for i in range(4)]),
        (600, 20, 60, 30, [(i * 30, (i + 1) * 30) for i in range(20)]),
    ],
)
def test_exact_folds_and_invariants(observations, k, train_min, val_min, validation_ranges):
    options = dict(min_train=train_min, min_validation=val_min)
    splits = one_sided_folds(observations, k, **options)
    again = one_sided_folds(observations, k, **options)
    for (train, validation), (start, end), repeated in zip(
        splits, validation_ranges, again, strict=True
    ):
        np.testing.assert_array_equal(validation, np.arange(start, end))
        expected_train = (
            np.arange(start) if start >= observations - end else np.arange(end, observations)
        )
        np.testing.assert_array_equal(train, expected_train)
        for indices, minimum, repeat in zip(
            (train, validation), (train_min, val_min), repeated, strict=True
        ):
            assert len(indices) >= minimum
            assert np.all(np.diff(indices) == 1)
            assert indices[0] >= 0 and indices[-1] < observations
            np.testing.assert_array_equal(indices, repeat)
        assert not np.intersect1d(train, validation).size
        assert train[-1] < validation[0] or train[0] > validation[-1]
    seen = np.concatenate([validation for _, validation in splits])
    assert len(np.unique(seen)) == len(seen)
    metadata = validation_metadata(observations, splits)
    assert metadata["validation_observations"] == len(seen)
    assert metadata["validation_coverage_fraction"] == len(seen) / observations
    if k >= 4 or observations >= 2 * train_min:
        np.testing.assert_array_equal(seen, np.arange(observations))
    else:
        np.testing.assert_array_equal(
            metadata["unvalidated_indices"],
            np.arange(validation_ranges[0][1], validation_ranges[1][0]),
        )
        assert np.intersect1d(splits[0][0], splits[1][0]).size > 0


@pytest.mark.parametrize("k", [0, 1, 3, 5, 19, 21, 2.0, True])
def test_unsupported_k(k):
    with pytest.raises(AnalyticsError, match=f"Requested K={k}.*even integers 2 through 20"):
        one_sided_folds(1200, k, min_train=120, min_validation=60)


@pytest.mark.parametrize(
    "k,t,train_min,val_min,required",
    [
        (4, 239, 120, 60, 240),
        (2, 179, 120, 60, 180),
        (4, 119, 60, 30, 120),
        (20, 599, 60, 30, 600),
    ],
)
def test_insufficient_history_errors(k, t, train_min, val_min, required):
    with pytest.raises(AnalyticsError) as error:
        one_sided_folds(t, k, min_train=train_min, min_validation=val_min)
    message = str(error.value)
    for text in [
        f"K={k}",
        f"observations={t}",
        f"minimum={required}",
        f"train size={train_min}",
        f"validation size={val_min}",
        "Eligible K",
    ]:
        assert text in message


@pytest.mark.parametrize("k", range(2, 21, 2))
def test_minimum_table(k):
    assert minimum_observations(k, min_train=120, min_validation=60) == (180 if k == 2 else k * 60)
    assert minimum_observations(k, min_train=60, min_validation=30) == (90 if k == 2 else k * 30)


def test_generic_minimums_and_tie_breaking():
    splits = one_sided_folds(20, 4, min_train=1, min_validation=1)
    assert np.intersect1d(splits[0][0], splits[1][0]).size > 0
    # K=4, T=6: second block has two rows before and after; earlier side wins.
    train, validation = one_sided_folds(6, 4, min_train=2, min_validation=1)[1]
    np.testing.assert_array_equal(train, [0, 1])
    np.testing.assert_array_equal(validation, [2, 3])
    for train_min, val_min in [(7, 3), (3, 7), (11, 2), (1, 1)]:
        for k in range(2, 21, 2):
            required = minimum_observations(k, min_train=train_min, min_validation=val_min)
            one_sided_folds(required, k, min_train=train_min, min_validation=val_min)
            with pytest.raises(AnalyticsError):
                one_sided_folds(required - 1, k, min_train=train_min, min_validation=val_min)
