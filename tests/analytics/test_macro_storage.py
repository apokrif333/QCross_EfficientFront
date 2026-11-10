from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.analytics.data_loader import attach_references, load_returns
from app.analytics.models import AnalyticsError
from app.analytics.provenance import panel_provenance
from app.db.models import Instrument, MonthlyReturn, ReturnSeries
from app.db.models.reference import ReferenceObservation, ReferenceRevision, ReferenceSeries
from app.ingestion.lazyportfolio.inflation import extract_inflation
from app.ingestion.lazyportfolio.macro_service import INFLATION, refresh
from tests.analytics.test_deterministic import seed_database


def seed_macro(session_factory, panel):
    seed_database(session_factory, panel)
    rows, nominal, snapshot = extract_inflation(
        Path("tests/fixtures/lazyportfolio_bil_inflation.html").read_text(encoding="utf-8"),
        source_url="BIL fixture",
        extracted_at=datetime.now(UTC),
    )
    with session_factory() as s:
        s.add(
            Instrument(
                id=99,
                source="lazyportfolioetf",
                source_symbol="BIL",
                ticker="BIL",
                currency="USD",
                source_url="https://www.lazyportfolioetf.com/etf/spdr-blmbg-barclays-1-3-mth-t-bill-bil/",
                is_active=True,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                raw_metadata={},
            )
        )
        s.flush()
        series = ReturnSeries(
            instrument_id=99,
            source="lazyportfolioetf",
            currency="USD",
            frequency="monthly",
            return_type="nominal_total_return",
            validation_status="validated",
            start_date=nominal.start_date,
            end_date=nominal.end_date,
            observation_count=len(rows),
            last_updated_at=datetime.now(UTC),
            source_metadata={},
        )
        s.add(series)
        s.flush()
        s.add_all(
            [
                MonthlyReturn(
                    series_id=series.id,
                    date=o.date,
                    return_value=o.return_value,
                    quality_flag="validated",
                    source_metadata={},
                )
                for o in nominal.observations
            ]
        )
        s.commit()
    return rows, nominal, snapshot


def test_reference_refresh_revisions_idempotence(session_factory, panel):
    from dataclasses import replace

    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as s:
        refresh(s, rows, nominal, snapshot)
        s.commit()
        series = s.scalar(select(ReferenceSeries).where(ReferenceSeries.series_code == INFLATION))
        version = series.data_version
        refresh(s, rows, nominal, snapshot)
        s.commit()
        assert series.data_version == version
        changed = rows.copy()
        changed[1500] = replace(changed[1500], value=changed[1500].value + Decimal(".001"))
        report = refresh(s, changed, nominal, "a" * 64)
        s.commit()
        assert report["pending_revisions"] == 1 and series.data_version == version
        refresh(s, changed, nominal, "a" * 64, accept_revisions=True)
        s.commit()
        assert series.data_version != version
        assert s.scalar(select(ReferenceRevision)).applied_at is not None
        # Provisional -> published transition is an explicitly resolved publication,
        # and can be applied without approving unrelated historical revisions.
        changed[-1] = replace(changed[-1], status="published", value=Decimal(".002"))
        refresh(s, changed, nominal, "b" * 64)
        s.commit()
        assert (
            s.scalar(
                select(ReferenceObservation).where(ReferenceObservation.month == rows[-1].month)
            ).status
            == "published"
        )


def test_alignment_transformation_fingerprints_and_unknown_gaps(session_factory, panel):
    import numpy as np

    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as s:
        refresh(s, rows, nominal, snapshot)
        s.commit()
        original = load_returns(s, [1, 2, 3])
        nominal_panel = attach_references(s, load_returns(s, [1, 2, 3]))
        real = attach_references(s, load_returns(s, [1, 2, 3]), return_basis="real")
        rates = np.array([r[1] for r in real.reference_metadata["inflation_observations"]])
        np.testing.assert_allclose((1 + real.values) * (1 + rates[:, None]), 1 + original.values)
        np.testing.assert_allclose(
            (1 + real.risk_free_returns) * (1 + rates), 1 + nominal_panel.risk_free_returns
        )
        old = panel_provenance(real)["reference_panel_sha256"]
        observation = s.scalar(
            select(ReferenceObservation).where(
                ReferenceObservation.month == original.returns.index[50].to_timestamp("M").date()
            )
        )
        observation.value += Decimal(".001")
        s.commit()
        updated = attach_references(s, load_returns(s, [1, 2, 3]), return_basis="real")
        assert panel_provenance(updated)["reference_panel_sha256"] != old
        assert (
            panel_provenance(attach_references(s, load_returns(s, [1, 2, 3])))[
                "reference_panel_sha256"
            ]
            == panel_provenance(nominal_panel)["reference_panel_sha256"]
        )
        s.delete(observation)
        s.commit()
        with pytest.raises(AnalyticsError, match="Unknown inflation gap"):
            attach_references(s, load_returns(s, [1, 2, 3]), return_basis="real")


@pytest.mark.parametrize(
    "endpoint", ["frontier", "cross-validation", "bootstrap", "resampled-frontier"]
)
@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("source", ["historical_bil", "fixed_annual"])
def test_macro_api_jobs(endpoint, basis, source, session_factory, panel, settings):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.analytics.test_api import wait_result

    rows, nominal, snapshot = seed_macro(session_factory, panel)
    with session_factory() as s:
        refresh(s, rows, nominal, snapshot)
        s.commit()
    with TestClient(create_app(settings)) as client:
        body = {
            "instrument_ids": [1, 2, 3],
            "return_basis": basis,
            "risk_free_source": source,
            "covariance_method": "sample",
            "frontier_points": 3,
            "bootstrap_iterations": 2,
            "objective": "max_sharpe",
        }
        preview = client.post("/api/v1/analytics/data-preview", json=body)
        assert preview.status_code == 200, preview.text
        submitted = client.post("/api/v1/analytics/" + endpoint, json=body)
        assert submitted.status_code == 202, submitted.text
        result = wait_result(client, submitted.json())
        assert result["status"] == "completed", result
        assert result["result"]["return_basis"] == basis
        assert result["result"]["risk_free_source"] == source


def test_new_default_and_legacy_fixed_semantics():
    from app.schemas.frontier import AnalyticsRequest

    assert AnalyticsRequest(instrument_ids=[1, 2]).risk_free_source == "historical_bil"
    assert (
        AnalyticsRequest(instrument_ids=[1, 2], risk_free_rate=0.03).risk_free_source
        == "fixed_annual"
    )
