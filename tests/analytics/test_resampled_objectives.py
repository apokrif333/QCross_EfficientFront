"""Global portfolio objectives must not change the shared risk-aversion curve."""

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analytics.bootstrap import analyze_resampled_frontier
from app.analytics.optimizer import PortfolioOptimizer
from app.ingestion.lazyportfolio.macro_service import refresh
from app.main import create_app
from app.schemas.frontier import AnalyticsRequest, ResampledFrontierRequest
from tests.analytics.test_api import wait_result
from tests.analytics.test_macro_storage import seed_macro


@pytest.mark.parametrize(
    "objective,target",
    [("gmv", None), ("max_sharpe", 0.08), ("target_return", None), ("target_return", 12)],
)
def test_resampled_curve_ignores_global_target(panel, monkeypatch, objective, target):
    def forbidden(*args, **kwargs):
        pytest.fail("Resampled Frontier must optimize shared risk aversion, not a global objective")

    monkeypatch.setattr(PortfolioOptimizer, "target_return", forbidden)
    monkeypatch.setattr(PortfolioOptimizer, "max_sharpe", forbidden)
    options = dict(
        frontier_points=3,
        bootstrap_iterations=4,
        risk_aversions=[50.0, 5.0, 0.5],
        asset_constraints={"1": {"max": 0.4}},
        group_constraints={"Equity": {"max": 0.7}},
    )
    baseline = analyze_resampled_frontier(panel, **options)
    result = analyze_resampled_frontier(panel, objective=objective, target_return=target, **options)
    assert result["status"] == "complete"
    assert result["sample_fingerprint"] == baseline["sample_fingerprint"]
    assert result["risk_aversions"] == baseline["risk_aversions"]
    for actual, expected in zip(result["frontier"], baseline["frontier"], strict=True):
        assert actual["weights"] == expected["weights"]
        assert actual["metrics"] == expected["metrics"]
        w = np.array(list(actual["weights"].values()))
        assert w.sum() == pytest.approx(1)
        assert w[0] <= 0.4 + 1e-7 and w[0] + w[2] <= 0.7 + 1e-7


def test_target_requirement_is_operation_aware():
    body = dict(
        instrument_ids=[1, 2],
        objective="target_return",
        bootstrap_objectives=["target_return"],
        target_return=None,
    )
    assert ResampledFrontierRequest(**body).target_return is None
    with pytest.raises(ValidationError, match="requires an annual target_return"):
        AnalyticsRequest(**body)


@pytest.mark.parametrize("basis", ["nominal", "real"])
def test_resampled_jobs_ignore_global_objective_and_preserve_provenance(
    panel, settings, session_factory, basis
):
    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as session:
        refresh(session, rows, nominal, snapshot)
        session.commit()
    baseline = None
    with TestClient(create_app(settings)) as client:
        for objective, target in [
            ("gmv", None),
            ("max_sharpe", 0.08),
            ("target_return", None),
            ("target_return", 12),
        ]:
            body = dict(
                instrument_ids=[1, 2, 3],
                risk_free_source="historical_bil",
                return_basis=basis,
                frontier_points=3,
                bootstrap_iterations=3,
                risk_aversions=[50.0, 5.0, 0.5],
                objective=objective,
                target_return=target,
                bootstrap_objectives=["target_return"],
            )
            submitted = client.post("/api/v1/analytics/resampled-frontier", json=body)
            assert submitted.status_code == 202, submitted.text
            finished = wait_result(client, submitted.json())
            assert finished["status"] == "completed", finished
            result = finished["result"]
            assert result["status"] == "complete"
            assert result["reproducibility"]["api_request"]["objective"] == objective
            assert result["reproducibility"]["api_request"]["target_return"] == target
            assert "objective" not in result["reproducibility"]["optimization_parameters"]
            assert "target_return" not in result["reproducibility"]["optimization_parameters"]
            if baseline is None:
                baseline = result
            assert result["sample_fingerprint"] == baseline["sample_fingerprint"]
            assert (
                result["reproducibility"]["calculation_sha256"]
                == baseline["reproducibility"]["calculation_sha256"]
            )
            for actual, expected in zip(result["frontier"], baseline["frontier"], strict=True):
                assert actual["weights"] == expected["weights"]
                assert actual["metrics"] == expected["metrics"]
