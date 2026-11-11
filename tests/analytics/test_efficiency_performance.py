import time

from app.analytics.bootstrap import analyze_bootstrap


def test_thousand_bootstrap_samples_with_exact_efficiency_distances(panel, record_property):
    started = time.perf_counter()
    result = analyze_bootstrap(
        panel,
        bootstrap_iterations=1000,
        objectives=["gmv", "max_sharpe", "target_return"],
        target_return=0.05,
        random_seed=42,
        user_weights=dict.fromkeys(panel.assets, 1 / len(panel.assets)),
    )
    elapsed = time.perf_counter() - started
    distances = result["efficiency_robustness"]
    assert len(distances["results"]) == 1000
    assert distances["valid_count"] == 1000
    assert distances["failed_count"] == 0
    assert distances["horizon"]["months"] == len(panel.values)
    assert distances["distributions"]["annualized_cagr"]["valid_count"] == 1000
    assert distances["distributions"]["terminal_wealth"]["valid_count"] == 1000
    assert elapsed < 300  # Existing bounded job timeout, including all three objectives.
    record_property("bootstrap_efficiency_1000_seconds", elapsed)
    print(f"1000 bootstrap iterations, all three objectives + exact distances: {elapsed:.3f}s")
