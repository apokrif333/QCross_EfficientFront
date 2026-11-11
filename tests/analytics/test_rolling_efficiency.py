from app.analytics.rolling_efficiency import analyze_rolling_efficiency


def test_rolling_windows_are_full_and_stepped_by_twelve(panel):
    result = analyze_rolling_efficiency(
        panel,
        user_weights={asset: 1 / len(panel.assets) for asset in panel.assets},
    )
    assert len(result["windows"]) == 11
    assert result["windows"][0]["start_month"] == str(panel.returns.index[0])
    assert result["windows"][0]["end_month"] == str(panel.returns.index[119])
    assert result["windows"][1]["start_month"] == str(panel.returns.index[12])
    assert all(window["observations"] == 120 for window in result["windows"])


def test_rolling_cv_linear_chooses_largest_eligible_k(panel):
    result = analyze_rolling_efficiency(
        panel,
        user_weights={asset: 1 / len(panel.assets) for asset in panel.assets},
        covariance_method="cv_linear",
        cv_folds=4,
    )
    assert result["effective_cv_folds"] == 2
    assert all(window["effective_cv_folds"] == 2 for window in result["windows"])


def test_rolling_reproducibility_includes_fixed_allocation_constraints_and_basis(panel):
    options = {"user_weights": dict.fromkeys(panel.assets, 1 / len(panel.assets))}
    original = analyze_rolling_efficiency(panel, **options)
    repeated = analyze_rolling_efficiency(panel, **options)
    assert (
        original["reproducibility"]["calculation_sha256"]
        == repeated["reproducibility"]["calculation_sha256"]
    )
    changed = analyze_rolling_efficiency(panel, asset_constraints={"1": {"max": 0.6}}, **options)
    assert (
        changed["reproducibility"]["calculation_sha256"]
        != original["reproducibility"]["calculation_sha256"]
    )
    changed = analyze_rolling_efficiency(panel, user_weights={panel.assets[0]: 1.0})
    assert (
        changed["reproducibility"]["calculation_sha256"]
        != original["reproducibility"]["calculation_sha256"]
    )
    from dataclasses import replace

    real = replace(panel, returns=(1 + panel.returns) / 1.002 - 1, return_basis="real")
    changed = analyze_rolling_efficiency(real, **options)
    assert (
        changed["reproducibility"]["calculation_sha256"]
        != original["reproducibility"]["calculation_sha256"]
    )


def test_rolling_partial_failure_is_explicit(panel, monkeypatch):
    import app.analytics.rolling_efficiency as module

    actual, calls = module.efficiency_distance, 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            return {
                "status": "solver_failed",
                "return_shortfall": None,
                "excess_volatility": None,
                "diagnostics": {"reason": "injected failure"},
            }
        return actual(*args, **kwargs)

    monkeypatch.setattr(module, "efficiency_distance", fail_once)
    result = analyze_rolling_efficiency(
        panel, user_weights=dict.fromkeys(panel.assets, 1 / len(panel.assets))
    )
    assert result["status"] == "incomplete"
    assert result["summary"]["valid_count"] == 10
    assert result["summary"]["failed_count"] == 1
