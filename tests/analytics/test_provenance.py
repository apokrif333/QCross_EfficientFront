"""Synthetic test panels; production histories are never changed."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.analytics.data_loader import load_returns
from app.analytics.provenance import panel_provenance
from app.db.models import MonthlyReturn
from app.main import create_app
from tests.analytics.test_deterministic import seed_database


def test_matrix_identity_includes_values_months_and_order(panel):
    identity = panel_provenance(panel)
    assert len(identity["returns_matrix_sha256"]) == 64
    assert identity == panel_provenance(panel)
    panel.returns.iloc[0, 0] += 0.001
    assert panel_provenance(panel)["returns_matrix_sha256"] != identity["returns_matrix_sha256"]
    panel.returns.iloc[0, 0] -= 0.001
    panel.assets.reverse()
    assert panel_provenance(panel)["returns_matrix_sha256"] != identity["returns_matrix_sha256"]


def test_source_version_identifies_observations_outside_requested_period(panel, session_factory):
    seed_database(session_factory, panel)
    with session_factory() as session:
        before = load_returns(session, [1, 2], start_date=date(2005, 1, 1))
        observation = session.get(MonthlyReturn, 1)
        observation.return_value += Decimal("0.001")
        session.commit()
        after = load_returns(session, [1, 2], start_date=date(2005, 1, 1))
    assert before.metadata[0]["series_version"] != after.metadata[0]["series_version"]
    assert (
        panel_provenance(before)["returns_matrix_sha256"]
        == panel_provenance(after)["returns_matrix_sha256"]
    )


def test_catalog_and_preview_are_read_only(panel, settings, session_factory):
    seed_database(session_factory, panel)
    app = create_app(settings)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/analytics/catalog").json()
        assert len(catalog["instruments"]) == 3
        assert "Equity" in catalog["instruments"][0]["groups"]
        preview = client.post("/api/v1/analytics/data-preview", json={"instrument_ids": [1, 2]})
        assert preview.status_code == 200
        result = preview.json()
        assert result["period"]["observations"] == 240
        assert result["reproducibility"]["source_series"][0]["series_version"]
        assert not app.state.analytics_jobs.jobs
        invalid = client.post(
            "/api/v1/analytics/data-preview",
            json={"instrument_ids": [1, 2], "start_date": "2018-01-01"},
        )
        assert invalid.status_code == 422 and "120" in invalid.text
