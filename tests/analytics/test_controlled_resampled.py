from dataclasses import replace

import numpy as np
import pytest

from app.analytics.bootstrap import analyze_resampled_frontier, stationary_indices
from app.analytics.portfolio_controls import compare_controls
from tests.analytics.test_controlled_bootstrap import USER, controlled_panel


@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("mode", ["downside_vol", "trend"])
def test_resampled_controlled_points_and_unchanged_curve(panel, method, basis, mode):
    p = controlled_panel(panel, mode, basis)
    options = dict(
        bootstrap_iterations=4,
        frontier_points=3,
        random_seed=42,
        covariance_method=method,
        user_weights=USER,
    )
    result = analyze_resampled_frontier(p, **options)
    passive = analyze_resampled_frontier(replace(p, control_config={}), **options)
    assert result["sample_fingerprint"] == passive["sample_fingerprint"]
    assert result["frontier"] == passive["frontier"]
    assert result["user_portfolio_mode"] == "controlled"
    assert result["user_portfolio"]["status"] == "historical_path"
    full = compare_controls(p, USER, record_history=False)["controlled"]["net"]
    metrics = result["user_portfolio"]["metrics"]
    assert metrics["expected_return"] == full["annualized_arithmetic_return"]
    assert metrics["volatility"] == full["annualized_volatility"]
    assert metrics["historical_cagr"] == full["annualized_cagr"]
    assert metrics["historical_sharpe_ratio"] == full["historical_sharpe_ratio"]
    assert metrics["expected_return"] != passive["user_portfolio"]["metrics"]["expected_return"]
    rng = np.random.default_rng(42)
    samples = [
        compare_controls(p, USER, stationary_indices(len(p.values), 12, rng), record_history=False)[
            "controlled"
        ]["net"]
        for _ in range(4)
    ]
    average = result["user_sample_average"]
    assert average["successful_samples"] == 4
    assert average["expected_return"] == pytest.approx(
        np.mean([s["annualized_arithmetic_return"] for s in samples])
    )
    assert average["volatility"] == pytest.approx(
        np.mean([s["annualized_volatility"] for s in samples])
    )


@pytest.mark.parametrize("all_fail", [False, True])
def test_control_failure_is_separate_and_never_uses_passive(panel, monkeypatch, all_fail):
    p = controlled_panel(panel)
    calls = 0

    def fail(panel, user_weights, indices=None, **kwargs):
        nonlocal calls
        if indices is not None:
            calls += 1
            if all_fail or calls == 1:
                raise ValueError("control path failed")
        return compare_controls(panel, user_weights, indices, **kwargs)

    monkeypatch.setattr("app.analytics.portfolio_controls.compare_controls", fail)
    result = analyze_resampled_frontier(
        p, bootstrap_iterations=4, frontier_points=3, user_weights=USER
    )
    assert result["status"] == "complete"
    assert len(result["frontier"]) == 3
    assert result["user_sample_diagnostics"]["failed_control_samples"] == (4 if all_fail else 1)
    assert all(
        r["reason"] == "control path failed" for r in result["user_sample_diagnostics"]["failures"]
    )
    if all_fail:
        assert result["user_sample_average"] is None
    else:
        assert result["user_sample_average"]["successful_samples"] == 3
