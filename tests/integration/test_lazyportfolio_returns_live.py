"""Run explicitly: python -m pytest -m network tests/integration."""

from datetime import date

import pytest

from app.config import Settings
from app.ingestion.lazyportfolio.client import LazyPortfolioClient
from app.ingestion.lazyportfolio.returns_parser import parse_returns
from app.ingestion.lazyportfolio.returns_service import VTI_PATH
from app.ingestion.lazyportfolio.returns_validation import validate_returns

pytestmark = pytest.mark.network


def test_live_vti_full_nominal_history() -> None:
    page = LazyPortfolioClient(Settings(_env_file=None)).fetch_page(VTI_PATH)
    data = parse_returns(
        page.html, symbol="VTI", currency="USD", source_url=page.url, extracted_at=page.fetched_at
    )
    report = validate_returns(data)
    assert report.passed, report.errors
    assert data.start_date == date(1793, 1, 31)
    assert data.end_date >= date(2026, 8, 31)
    assert len(data.observations) >= 2804
