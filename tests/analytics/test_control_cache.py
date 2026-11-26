from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from app.analytics.bootstrap import analyze_resampled_frontier, stationary_indices
from app.analytics.control_cache import export_control_cache, indices_hash
from app.analytics.portfolio_controls import compare_controls
from app.schemas.frontier import ResampledFrontierRequest
from tests.analytics.test_controlled_bootstrap import USER, controlled_panel


@pytest.fixture
def stored(panel):
    p = controlled_panel(panel)
    p.simulation_options["transaction_fee_rate"] = 0.0
    rng = np.random.default_rng(42)
    samples = []
    for _ in range(4):
        indices = stationary_indices(len(p.values), 12, rng)
        samples.append(
            {
                **compare_controls(p, USER, indices, record_history=False),
                "indices_sha256": indices_hash(indices),
            }
        )
    return p, export_control_cache(
        p,
        USER,
        {
            "portfolio_control_samples": samples,
            "portfolio_controls": compare_controls(p, USER),
            "expected_block_length": 12,
            "random_seed": 42,
        },
    )


def run(panel, cache, count=4, **options):
    return analyze_resampled_frontier(
        panel,
        bootstrap_control_cache=cache,
        user_weights=options.pop("user_weights", USER),
        bootstrap_iterations=count,
        frontier_points=3,
        **options,
    )


def test_prefix_reuses_saved_paths_without_any_signal_calculation(stored, monkeypatch):
    panel, cache = stored
    expected = run(panel, None, count=2)

    def forbidden(*args, **kwargs):
        raise AssertionError("Signals must not be recalculated")

    monkeypatch.setattr("app.analytics.portfolio_controls.compare_controls", forbidden)
    result = run(panel, cache, count=2)
    assert result["user_portfolio"] == expected["user_portfolio"]
    assert result["user_sample_average"] == expected["user_sample_average"]
    assert result["frontier"] == expected["frontier"]
    assert result["sample_fingerprint"] == expected["sample_fingerprint"]
    assert result["user_sample_diagnostics"]["reused_samples"] == 2
    assert result["user_sample_diagnostics"]["computed_samples"] == 0


@pytest.mark.parametrize(
    "change", ["seed", "block", "fee", "tax", "data", "hedge", "basis", "weights"]
)
def test_incompatible_data_or_settings_are_not_reused(stored, change):
    p, cache = stored
    options = {}
    if change == "seed":
        options["random_seed"] = 7
    if change == "block":
        options["expected_block_length"] = 6
    if change == "fee":
        options["transaction_fee_rate"] = 0.001
    if change == "tax":
        options["capital_gains_tax_rate"] = 0.10
    if change == "data":
        frame = p.returns.copy()
        frame.iloc[0, 0] += 0.01
        p = replace(p, returns=frame)
    if change == "hedge":
        frame = p.control_returns.copy()
        frame.iloc[0, -1] += 0.01
        p = replace(p, control_returns=frame)
    if change == "basis":
        p = controlled_panel(p, basis="real")
    if change == "weights":
        options["user_weights"] = {"1": 0.4, "2": 0.3, "3": 0.3}
    r = run(p, cache, **options)
    assert r["user_sample_diagnostics"]["reused_samples"] == 0
    assert r["user_sample_diagnostics"]["computed_samples"] == 4


def test_partial_cache_and_index_mismatch_compute_only_missing_paths(stored):
    p, cache = stored
    cache = deepcopy(cache)
    cache["samples"] = cache["samples"][:2]
    cache["samples"][0]["indices_sha256"] = "0" * 64
    r = run(p, cache)
    assert r["user_sample_diagnostics"]["reused_samples"] == 1
    assert r["user_sample_diagnostics"]["computed_samples"] == 3
    expected = run(p, None)
    assert r["user_sample_average"] == expected["user_sample_average"]


def test_failed_saved_control_paths_remain_failures(stored):
    p, cache = stored
    cache = deepcopy(cache)
    cache["samples"][0].update(expected_return=None, volatility=None, error="saved failure")
    r = run(p, cache)
    assert r["status"] == "complete"
    assert r["user_sample_average"]["successful_samples"] == 3
    assert r["user_sample_diagnostics"]["failures"] == [{"iteration": 0, "reason": "saved failure"}]
    assert r["user_sample_diagnostics"]["computed_samples"] == 0


def test_schema_accepts_portable_cache(stored):
    _, cache = stored
    body = ResampledFrontierRequest(
        instrument_ids=[1, 2, 3], user_weights=USER, bootstrap_control_cache=cache
    )
    assert body.bootstrap_control_cache.model_dump() == cache


def test_api_keeps_resampled_frontier_scenarios_separate(panel, settings, session_factory):
    from fastapi.testclient import TestClient

    from app.ingestion.lazyportfolio.macro_service import refresh
    from app.main import create_app
    from tests.analytics.test_api import wait_result
    from tests.analytics.test_macro_storage import seed_macro

    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as session:
        refresh(session, rows, nominal, snapshot)
        session.commit()
    body = dict(
        instrument_ids=[1, 2, 3],
        user_weights=USER,
        bootstrap_iterations=4,
        frontier_points=3,
        bootstrap_objectives=["gmv"],
        portfolio_controls={
            "mode": "downside_vol",
            "hedge_weights": {"BIL_REFERENCE": 1},
            "downside_vol": {"target_annual": 0.06, "lookback_months": 1},
        },
    )
    with TestClient(create_app(settings)) as client:
        job = client.post("/api/v1/analytics/bootstrap", json=body)
        assert job.status_code == 202, job.text
        done = wait_result(client, job.json())
        assert done["status"] == "completed", done
        assert "portfolio_control_cache" not in done["result"]
        job = client.post(
            "/api/v1/analytics/resampled-frontier",
            json={
                **body,
                "bootstrap_iterations": 2,
            },
        )
        assert job.status_code == 202, job.text
        done = wait_result(client, job.json())
        assert done["status"] == "completed", done
        assert done["result"]["user_sample_diagnostics"]["reused_samples"] == 0
        assert done["result"]["user_sample_diagnostics"]["computed_samples"] == 2
        assert "bootstrap_control_cache" not in done["result"]["reproducibility"]["api_request"]


def test_only_frontier_successful_generations_contribute(stored, monkeypatch):
    from app.analytics.models import OptimizationResult
    from app.analytics.optimizer import PortfolioOptimizer

    p, cache = stored
    utility, calls = PortfolioOptimizer.utility, 0

    def fail_one(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return (
            OptimizationResult("solver_failed", None, {})
            if calls == 1
            else utility(self, *args, **kwargs)
        )

    monkeypatch.setattr(PortfolioOptimizer, "utility", fail_one)
    r = run(p, cache)
    assert r["user_sample_average"]["successful_samples"] == 3
    assert r["user_sample_diagnostics"]["reused_samples"] == 3
    assert r["user_sample_diagnostics"]["excluded_frontier_samples"] == 1
