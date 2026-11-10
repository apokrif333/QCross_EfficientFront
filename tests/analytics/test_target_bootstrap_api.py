import pytest
from fastapi.testclient import TestClient

from app.ingestion.lazyportfolio.macro_service import refresh
from app.main import create_app
from tests.analytics.test_api import wait_result
from tests.analytics.test_deterministic import seed_database
from tests.analytics.test_macro_storage import seed_macro


@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("source", ["historical_bil", "fixed_annual"])
def test_target_bootstrap_job_preserves_metadata(panel, settings, session_factory, basis, source):
    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as session:
        refresh(session, rows, nominal, snapshot)
        session.commit()
    body = dict(
        instrument_ids=[1, 2, 3],
        return_basis=basis,
        risk_free_source=source,
        objective="target_return",
        target_return=0.03,
        bootstrap_objectives=["gmv", "max_sharpe", "target_return"],
        bootstrap_iterations=3,
    )
    with TestClient(create_app(settings)) as client:
        submitted = client.post("/api/v1/analytics/bootstrap", json=body)
        assert submitted.status_code == 202, submitted.text
        finished = wait_result(client, submitted.json())
        assert finished["status"] == "completed", finished
        result = finished["result"]
        assert result["return_basis"] == basis and result["risk_free_source"] == source
        target = result["objectives"]["target_return"]
        assert target["target_return"] == 0.03
        assert target["computational_status"] == "complete"
        assert target["target_feasibility_rate"] == target["target_feasible_iterations"] / 3
        assert target["numerical_failure_iterations"] == 0
        assert target["statistics_condition"] == "successful_target_feasible_samples"
        assert (
            result["reproducibility"]["api_request"]["bootstrap_objectives"]
            == body["bootstrap_objectives"]
        )


def test_invalid_target_bootstrap_is_rejected_before_jobs(panel, settings, session_factory):
    seed_database(session_factory, panel)
    app = create_app(settings)
    with TestClient(app) as client:
        for extra in [
            dict(bootstrap_objectives=["target_return"]),
            dict(bootstrap_objectives=["gmv", "gmv"]),
            dict(objective="target_return"),
            dict(bootstrap_objectives=["target_return"], target_return=13),
        ]:
            response = client.post(
                "/api/v1/analytics/bootstrap",
                json={"instrument_ids": [1, 2], "risk_free_source": "fixed_annual", **extra},
            )
            assert response.status_code == 422, response.text
        assert not app.state.analytics_jobs.jobs


def test_original_and_samples_infeasible_are_completed_results(panel, settings, session_factory):
    seed_database(session_factory, panel)
    with TestClient(create_app(settings)) as client:
        submitted = client.post(
            "/api/v1/analytics/bootstrap",
            json={
                "instrument_ids": [1, 2],
                "risk_free_source": "fixed_annual",
                "objective": "target_return",
                "target_return": 2,
                "bootstrap_iterations": 3,
            },
        )
        assert submitted.status_code == 202
        finished = wait_result(client, submitted.json())
        assert finished["status"] == "completed"
        target = finished["result"]["objectives"]["target_return"]
        assert target["target_feasibility_rate"] == 0
        assert target["status"] == "complete"
        assert target["original_target_feasible"] is False
        assert target["target_infeasible_iterations"] == 3
        assert target["resampled"] is target["stability"] is None
