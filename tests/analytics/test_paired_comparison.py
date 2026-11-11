import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.analytics.bootstrap import analyze_bootstrap, stationary_indices
from app.analytics.constraints import build_constraints
from app.analytics.cross_validation import analyze_cross_validation
from app.analytics.models import AnalyticsError, OptimizationResult, ReturnPanel
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.paired_comparison import compare_validation, summarize, validation_metrics
from app.ingestion.lazyportfolio.macro_service import refresh
from app.main import create_app
from tests.analytics.test_api import wait_result
from tests.analytics.test_macro_storage import seed_macro

USER = {"1": 0.6, "2": 0.2, "3": 0.2}


def test_risk_limit_and_infeasible_user():
    cov = np.diag([0.04, 0.09])
    engine = PortfolioOptimizer(np.array([0.05, 0.12]), cov, build_constraints(["1", "2"]))
    user = np.array([0.5, 0.5])
    result = engine.max_return_at_user_risk(user)
    w = result.require_weights()
    assert w @ cov @ w <= user @ cov @ user + 1e-7
    assert engine.mean @ w >= engine.mean @ user - 1e-7
    with pytest.raises(AnalyticsError):
        engine.max_return_at_user_risk(np.array([0.8, 0.8]))


@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
@pytest.mark.parametrize("basis", ["nominal", "real"])
def test_paired_sampling_alignment_and_moments(panel, method, basis):
    values = panel.values.copy()
    rf = values[:, 0] * 0.05 + 0.001
    if basis == "real":
        inflation = 0.002 + 0.001 * np.sin(np.arange(len(values)))
        values = (1 + values) / (1 + inflation[:, None]) - 1
        rf = (1 + rf) / (1 + inflation) - 1
    panel = ReturnPanel(
        panel.returns.copy(), panel.assets, panel.groups, risk_free_returns=rf, return_basis=basis
    )
    panel.returns.iloc[:, :] = values
    options = dict(
        bootstrap_iterations=6,
        objectives=["gmv"],
        covariance_method=method,
        user_weights=USER,
        random_seed=42,
    )
    result = analyze_bootstrap(panel, **options)
    pairs = result["paired_comparison"]
    assert pairs["generated_datasets"] == 6
    assert pairs["requested_comparisons"] == 3
    assert pairs["valid_count"] == 3
    assert result["efficiency_robustness"]["distributions"]["annualized_cagr"]["valid_count"] == 6
    rng = np.random.default_rng(42)
    means, covs = [], []
    for i in range(3):
        train = stationary_indices(len(values), 12, rng)
        valid = stationary_indices(len(values), 12, rng)
        assert not np.array_equal(train, valid)
        record = pairs["results"][i]
        expected = validation_metrics(values[valid], np.array(list(USER.values())), rf[valid])
        assert record["user"] == pytest.approx(expected)
        opt = validation_metrics(
            values[valid], np.array(list(record["weights"].values())), rf[valid]
        )
        assert record["optimized"] == pytest.approx(opt)
        assert record["differences"]["delta_cagr"] == pytest.approx(
            opt["annualized_cagr"] - expected["annualized_cagr"]
        )
        means.append(values[valid].mean(0) * 12)
        covs.append(np.cov(values[valid], rowvar=False, ddof=1) * 12)
    np.testing.assert_allclose(pairs["annual_mean"], np.mean(means, axis=0))
    np.testing.assert_allclose(pairs["annual_covariance"], np.mean(covs, axis=0))
    assert len(pairs["clouds"]) == 3 and len(pairs["validation_frontier"]) > 40


def test_cv_no_leakage_and_both_directions(panel):
    options = dict(cv_folds=4, user_weights=USER, covariance_method="cv_linear")
    baseline = analyze_cross_validation(panel, **options)
    changed = panel.returns.copy()
    changed.iloc[:60] += 0.03
    updated = analyze_cross_validation(ReturnPanel(changed, panel.assets, panel.groups), **options)
    original = baseline["paired_comparison"]["results"][0]
    revised = updated["paired_comparison"]["results"][0]
    assert original["weights"] == revised["weights"]
    assert original["user"] != revised["user"]
    assert {r["training_direction"] for r in baseline["paired_comparison"]["results"]} == {
        "train_before_validation",
        "train_after_validation",
    }


def test_optional_failure_does_not_change_legacy_results(panel, monkeypatch):
    options = dict(bootstrap_iterations=4, user_weights=USER, objectives=["gmv"])
    baseline = analyze_bootstrap(panel, include_paired_comparison=False, **options)
    monkeypatch.setattr(
        PortfolioOptimizer,
        "max_return_at_user_risk",
        lambda *a: OptimizationResult("solver_failed", None, {}),
    )
    result = analyze_bootstrap(panel, **options)
    assert result["paired_comparison"]["failed_count"] == 2
    assert result["sample_fingerprint"] == baseline["sample_fingerprint"]
    assert (
        result["objectives"]["gmv"]["resampled"]["weights"]
        == baseline["objectives"]["gmv"]["resampled"]["weights"]
    )
    with pytest.raises(AnalyticsError, match="even"):
        analyze_bootstrap(panel, bootstrap_iterations=3, user_weights=USER)


def test_zero_sharpe_denominator_and_delta_drawdown(panel):
    user = np.array([0.6, 0.2, 0.2])
    record = compare_validation(
        panel.values,
        panel.values @ user,
        user,
        OptimizationResult("optimal", user, {}),
        panel.assets,
    )
    summary = summarize([record])
    assert record["differences"]["delta_max_drawdown"] == 0
    assert summary["win_frequencies"]["higher_sharpe"]["denominator"] == 0


def test_500_datasets_have_250_pairs_and_500_user_paths(panel):
    started = time.perf_counter()
    result = analyze_bootstrap(
        panel, bootstrap_iterations=500, user_weights=USER, objectives=["gmv"]
    )
    assert result["paired_comparison"]["valid_count"] == 250
    assert result["paired_comparison"]["generated_datasets"] == 500
    for key, data in result["efficiency_robustness"]["distributions"].items():
        assert data["valid_count"] == 500, key
    print(f"500 datasets / 250 pairs: {time.perf_counter() - started:.3f} seconds")


@pytest.mark.parametrize("basis", ["nominal", "real"])
def test_paired_jobs_and_new_objective(panel, settings, session_factory, basis):
    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as session:
        refresh(session, rows, nominal, snapshot)
        session.commit()
    with TestClient(create_app(settings)) as client:
        body = dict(
            instrument_ids=[1, 2, 3],
            user_weights=USER,
            objective="max_return_at_user_risk",
            return_basis=basis,
            risk_free_source="historical_bil",
            frontier_points=3,
            bootstrap_iterations=4,
            bootstrap_objectives=["max_return_at_user_risk"],
        )
        for endpoint in ["frontier", "cross-validation", "bootstrap", "resampled-frontier"]:
            submitted = client.post(f"/api/v1/analytics/{endpoint}", json=body)
            assert submitted.status_code == 202, submitted.text
            done = wait_result(client, submitted.json())
            assert done["status"] == "completed", done
            result = done["result"]
            if endpoint in {"cross-validation", "bootstrap"}:
                assert result["paired_comparison"]["valid_count"] == (
                    4 if endpoint == "cross-validation" else 2
                )
        invalid = {**body, "bootstrap_iterations": 3}
        assert client.post("/api/v1/analytics/bootstrap", json=invalid).status_code == 422
        invalid = {**body, "asset_constraints": {"1": {"max": 0.2}}}
        assert client.post("/api/v1/analytics/bootstrap", json=invalid).status_code == 422
