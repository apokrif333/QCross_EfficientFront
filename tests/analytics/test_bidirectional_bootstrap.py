from dataclasses import replace

import numpy as np
import pytest

from app.analytics.bootstrap import analyze_bootstrap, bootstrap_generation
from app.analytics.control_cache import indices_hash
from app.analytics.estimators import select_shrinkage
from app.analytics.models import AnalyticsError
from app.analytics.portfolio_simulator import simulate_portfolio
from app.analytics.splitting import bootstrap_folds


@pytest.mark.parametrize("months,sizes", [(608, [152] * 4), (611, [153, 153, 153, 152])])
def test_bootstrap_source_ranges_and_full_coverage(months, sizes):
    splits = bootstrap_folds(months, 4, "sample")
    assert [len(v) for _, v in splits] == sizes
    np.testing.assert_array_equal(np.concatenate([v for _, v in splits]), np.arange(months))
    if months == 608:
        assert [(t[0], t[-1], len(t)) for t, _ in splits] == [
            (152, 607, 456),
            (304, 607, 304),
            (0, 303, 304),
            (0, 455, 456),
        ]
    for generation in range(5):
        saved = list(bootstrap_generation(splits, 12, 42, generation))
        again = list(bootstrap_generation(splits, 12, 42, generation))
        for (train, val), (source_train, source_val), (t, v) in zip(
            saved, splits, again, strict=True
        ):
            assert len(train) == len(source_train) and len(val) == len(source_val)
            assert set(train) <= set(source_train)
            assert set(val) <= set(source_val)
            assert not set(train) & set(val)
            np.testing.assert_array_equal(train, t)
            np.testing.assert_array_equal(val, v)
        assert sum(len(v) for _, v in saved) == months


def test_rejects_incomplete_outer_and_short_inner_cv(monkeypatch):
    with pytest.raises(AnalyticsError, match="full Validation coverage"):
        bootstrap_folds(239, 2, "sample")
    assert len(bootstrap_folds(360, 6, "cv_linear")) == 6
    monkeypatch.setattr("app.analytics.splitting.MIN_COVARIANCE_VALIDATION", 31)
    with pytest.raises(AnalyticsError, match="Inner covariance CV"):
        bootstrap_folds(360, 6, "cv_linear")


def test_odd_generations_replay_exact_validation_across_estimators(panel):
    returns = panel.returns.copy()
    returns.loc[returns.index[-1] + 1] = [0.01, 0.02, 0.03]
    panel = replace(panel, returns=returns)
    retained = []
    options = dict(bootstrap_iterations=3, objectives=["gmv"], random_seed=9)
    a = analyze_bootstrap(panel, _sample_indices=retained, **options)
    b = analyze_bootstrap(panel, covariance_method="ledoit_wolf", **options)
    assert a["sample_fingerprint"] == b["sample_fingerprint"]
    assert a["validation_sample_fingerprint"] == b["validation_sample_fingerprint"]
    assert len(retained) == len(a["generations"]) == 3
    for generation, draw in zip(a["generations"], retained, strict=True):
        assert len(draw) == len(returns) == 241
        assert generation["validation_sha256"] == indices_hash(draw)
        assert all(f["shared_source_months"] == 0 for f in generation["folds"])


def test_continuous_scheduled_path_matches_independent_cash_accounting():
    values = np.array([[0.20, 0], [0.05, 0], [0, -0.12], [0, 0.03]])
    rf = np.array([0.002, 0.003, 0.004, 0.001])
    fee = 0.01
    path = simulate_portfolio(
        values,
        [1, 0],
        rf,
        target_schedule={2: [0, 1]},
        rebalancing_interval_months=2,
        transaction_fee_rate=fee,
    )
    invested = 1 / (1 + fee)
    before_switch = invested * 1.20 * 1.05
    after_switch = before_switch * (1 - fee) / (1 + fee)
    expected = np.array(
        [1, invested * 1.20, after_switch, after_switch * 0.88, after_switch * 0.88 * 1.03]
    )
    np.testing.assert_allclose(path["net_wealth"], expected, rtol=1e-12)
    np.testing.assert_allclose(path["positions"][2], [0, after_switch], rtol=1e-12, atol=1e-14)
    assert [event["kind"] for event in path["events"]] == ["initial_purchase", "rebalance"]
    assert path["events"][1]["reason"] == "fold_target_change"
    assert path["execution_target_adjustment"] == 0
    assert path["total_commissions"] == pytest.approx(1 - invested + before_switch - after_switch)
    assert path["annualized_commissions"] == pytest.approx(
        (1 - invested + (before_switch - after_switch) / before_switch) * 3
    )
    net = path["net"]
    differential = expected[1:] / expected[:-1] - 1 - rf
    assert net["annualized_cagr"] == pytest.approx(expected[-1] ** 3 - 1)
    assert net["historical_sharpe_ratio"] == pytest.approx(
        np.sqrt(12) * differential.mean() / differential.std(ddof=1)
    )
    assert net["maximum_drawdown"] == pytest.approx(
        np.max(1 - expected / np.maximum.accumulate(expected))
    )
    assert net["calmar_ratio"] == pytest.approx(net["annualized_cagr"] / net["maximum_drawdown"])


def test_batched_shrinkage_model_selection_matches_scalar_likelihood(panel):
    estimates = [select_shrinkage(panel.values, 4, target) for target in ("identity", "diagonal")]
    expected = min(estimates, key=lambda estimate: min(estimate["nll_scores"]))
    actual = select_shrinkage(panel.values, 4, "auto")
    assert (actual["target"], actual["intensity"]) == (expected["target"], expected["intensity"])
    np.testing.assert_allclose(actual["nll_scores"], expected["nll_scores"], atol=1e-10)
