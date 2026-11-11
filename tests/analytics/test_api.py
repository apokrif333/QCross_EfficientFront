import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.frontier import AnalyticsRequest
from tests.analytics.test_deterministic import seed_database


def wait_result(client, job, timeout=30):
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        response = client.get(job["result_url"])
        assert response.status_code == 200
        result = response.json()
        if result["status"] != "running":
            return result
        time.sleep(0.05)
    pytest.fail("Analytics job did not reach a terminal status.")


@pytest.mark.parametrize(
    "endpoint", ["frontier", "cross-validation", "bootstrap", "resampled-frontier"]
)
def test_real_process_api_roundtrip(endpoint, panel, settings, session_factory):
    seed_database(session_factory, panel)
    app = create_app(settings)
    body = {
        "risk_free_source": "fixed_annual",
        "instrument_ids": [3, 1, 2],
        "frontier_points": 3,
        "bootstrap_iterations": 3,
        "include_paired_comparison": False,
        "asset_constraints": {"1": {"max": 0.4}},
        "group_constraints": {"Equity": {"max": 1.0}, "Group 3": {"min": 0.1}},
        "asset_groups": {"1": 1, "2": 3, "3": 5},
        "user_weights": {"1": 1.0},
    }
    with TestClient(app) as client:
        response = client.post(f"/api/v1/analytics/{endpoint}", json=body)
        assert response.status_code == 202
        job = response.json()
        assert client.get(job["status_url"]).status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        completed = wait_result(client, job)
        assert completed["status"] == "completed", completed
        assert completed["result"]["status"] == "complete"
        assert completed["result"]["assets"] == ["3", "1", "2"]
        assert completed["result"]["currency"] == "USD"
        assert completed["result"]["execution_seconds"] > 0
        assert "period" in completed["result"] and "correlation" in completed["result"]
        assert completed["result"]["allocation_groups"]["Group 3"] == ["2"]
        if endpoint == "frontier":
            user = completed["result"]["user_portfolio"]
            assert user["weights"]["1"] == 1.0
            assert not user["feasible"]
        if endpoint == "bootstrap":
            robustness = completed["result"]["efficiency_robustness"]
            assert robustness["horizon"]["months"] == len(panel.values)
            assert robustness["distributions"]["annualized_cagr"]["valid_count"] == 3
            assert robustness["distributions"]["terminal_wealth"]["horizon_years"] == 20
            assert robustness["distributions"]["return_shortfall"]["valid_count"] == 0
        provenance = completed["result"]["reproducibility"]
        expected = AnalyticsRequest.model_validate(body).model_dump(mode="json")
        if endpoint == "bootstrap":
            expected["bootstrap_objectives"] = ["gmv", "max_sharpe"]
        assert provenance["api_request"] == expected
        assert len(provenance["returns_matrix_sha256"]) == 64
        assert provenance["source_series"][0]["instrument_id"] == 3
        assert len(provenance["source_series"][0]["series_version"]) == 64
        assert client.get("/api/v1/analytics/jobs/not-a-job").status_code == 404
        assert client.get("/api/v1/analytics/jobs/not-a-job/result").status_code == 404


def test_api_rejects_invalid_inputs_before_starting_workers(panel, settings, session_factory):
    seed_database(session_factory, panel)
    app = create_app(settings)
    with TestClient(app) as client:
        cases = [
            {"currency": "EUR"},
            {"risk_free_source": "fixed_annual", "instrument_ids": [1, 1]},
            {"cv_folds": 11},
            {"asset_constraints": {"1": {"min": 0.8}, "2": {"min": 0.8}}},
            {"group_constraints": {"unknown": {"max": 0.1}}},
            {"objective": "target_return"},
            {"bootstrap_iterations": 2001},
            {"start_date": "2019-01-01"},
            {"start_date": "2021-01-01", "end_date": "2020-01-01"},
        ]
        for override in cases:
            response = client.post(
                "/api/v1/analytics/frontier",
                json={"risk_free_source": "fixed_annual", "instrument_ids": [1, 2], **override},
            )
            assert response.status_code == 422, response.text
        assert len(app.state.analytics_jobs.jobs) == 0
        response = client.post(
            "/api/v1/analytics/resampled-frontier",
            json={
                "risk_free_source": "fixed_annual",
                "instrument_ids": [1, 2],
                "frontier_points": 201,
                "bootstrap_iterations": 500,
            },
        )
        assert response.status_code == 422 and "30,000" in response.text


def test_rolling_efficiency_async_job_roundtrip(panel, settings, session_factory):
    seed_database(session_factory, panel)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/analytics/rolling-efficiency",
            json={
                "risk_free_source": "fixed_annual",
                "instrument_ids": [1, 2, 3],
                "user_weights": {"1": 0.4, "2": 0.3, "3": 0.3},
                "rolling_window_months": 120,
                "rolling_step_months": 12,
            },
        )
        assert response.status_code == 202, response.text
        result = wait_result(client, response.json())
        assert result["status"] == "completed", result
        assert len(result["result"]["windows"]) == 11
        assert result["result"]["windows"][0]["observations"] == 120


def test_rolling_efficiency_requires_user_weights_before_submission(
    panel, settings, session_factory
):
    seed_database(session_factory, panel)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/analytics/rolling-efficiency",
            json={"risk_free_source": "fixed_annual", "instrument_ids": [1, 2]},
        )
        assert response.status_code == 422
        assert "user_weights" in response.text


def test_api_capacity_and_timeout_are_bounded(panel, settings, session_factory):
    seed_database(session_factory, panel)
    settings.analytics_workers = 1
    settings.analytics_timeout_seconds = 1
    app = create_app(settings)
    with TestClient(app) as client:
        job = client.post(
            "/api/v1/analytics/bootstrap",
            json={
                "risk_free_source": "fixed_annual",
                "instrument_ids": [1, 2],
                "bootstrap_iterations": 2000,
            },
        ).json()
        second = client.post(
            "/api/v1/analytics/bootstrap",
            json={"risk_free_source": "fixed_annual", "instrument_ids": [1, 2]},
        )
        assert second.status_code == 429 and second.headers["Retry-After"] == "5"
        started = time.monotonic()
        assert client.get("/health").status_code == 200
        assert time.monotonic() - started < 0.5
        terminal = wait_result(client, job)
        assert terminal["status"] == "timed_out" and terminal["result"] is None
        assert "timeout" in terminal["error"]
        assert app.state.analytics_jobs.jobs[job["job_id"]].finished is not None


def test_worker_error_is_retrievable(panel, settings, session_factory):
    panel.returns["2"] = panel.returns["1"]
    seed_database(session_factory, panel)
    with TestClient(create_app(settings)) as client:
        job = client.post(
            "/api/v1/analytics/frontier",
            json={
                "risk_free_source": "fixed_annual",
                "instrument_ids": [1, 2],
                "covariance_method": "nonlinear",
            },
        ).json()
        terminal = wait_result(client, job)
        assert terminal["status"] == "failed"
        assert "singular" in terminal["error"]


def test_request_finiteness_and_defaults():
    from pydantic import ValidationError

    from app.schemas.frontier import ResampledFrontierRequest

    assert AnalyticsRequest(instrument_ids=[1, 2]).bootstrap_iterations == 1000
    assert AnalyticsRequest(instrument_ids=[1, 2]).cv_folds == 4
    for k in range(2, 21, 2):
        assert AnalyticsRequest(instrument_ids=[1, 2], cv_folds=k).cv_folds == k
    for k in [0, 1, 3, 5, 19, 21, 4.0, True]:
        with pytest.raises(ValidationError):
            AnalyticsRequest(instrument_ids=[1, 2], cv_folds=k)
    assert ResampledFrontierRequest(instrument_ids=[1, 2]).bootstrap_iterations == 250
    for value in [float("nan"), float("inf")]:
        with pytest.raises(ValidationError):
            AnalyticsRequest(instrument_ids=[1, 2], risk_free_rate=value)


def test_cv_api_minimum_history_rejected_before_submission(panel, settings, session_factory):
    seed_database(session_factory, panel)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analytics/cross-validation",
            json={
                "risk_free_source": "fixed_annual",
                "instrument_ids": [1, 2],
                "start_date": "2008-01-01",
                "cv_folds": 4,
            },
        )
        assert response.status_code == 422 and "required minimum=240" in response.text
        assert len(app.state.analytics_jobs.jobs) == 0


@pytest.mark.parametrize(
    "endpoint", ["frontier", "cross-validation", "bootstrap", "resampled-frontier"]
)
def test_covariance_history_rejected_before_job_submission(
    endpoint, panel, settings, session_factory
):
    seed_database(session_factory, panel)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/analytics/{endpoint}",
            json={
                "risk_free_source": "fixed_annual",
                "instrument_ids": [1, 2],
                "covariance_method": "cv_linear",
                "cv_folds": 20,
            },
        )
        assert response.status_code == 422
        assert "required minimum=" in response.text
        assert "actual observations=240" in response.text
        assert len(app.state.analytics_jobs.jobs) == 0


@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
def test_frontier_still_supports_120_months(method, panel, settings, session_factory):
    seed_database(session_factory, panel)
    app = create_app(settings)
    with TestClient(app) as client:
        body = {
            "risk_free_source": "fixed_annual",
            "instrument_ids": [1, 2],
            "start_date": "2010-01-01",
            "covariance_method": method,
            "cv_folds": 4,
            "frontier_points": 3,
        }
        preview = client.post("/api/v1/analytics/data-preview", json=body).json()
        assert preview["period"]["observations"] == 120
        assert preview["eligible_portfolio_folds"] == []
        assert preview["eligible_covariance_folds"] == [2, 4]
        response = client.post("/api/v1/analytics/frontier", json=body)
        assert response.status_code == 202, response.text
        assert wait_result(client, response.json())["status"] == "completed"
        rejected = client.post("/api/v1/analytics/cross-validation", json=body)
        assert rejected.status_code == 422 and "minimum=240" in rejected.text


def test_worker_checks_cv_configuration_without_api(panel):
    from app.analytics.jobs import _worker

    class Connection:
        payload = None

        def send(self, payload):
            self.payload = payload

        def close(self):
            pass

    connection = Connection()
    _worker(connection, panel, "cross-validation", {"cv_folds": 20})
    assert "required minimum=1200" in connection.payload["error"]
