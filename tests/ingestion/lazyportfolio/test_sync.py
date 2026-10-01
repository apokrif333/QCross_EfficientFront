from datetime import UTC, datetime, timedelta

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import IngestionState, Instrument
from app.ingestion.base import IngestionError, InstrumentRecord
from app.ingestion.lazyportfolio.client import FetchedPage, FetchError
from app.ingestion.lazyportfolio.service import SOURCE, DiscoveryRun, LazyPortfolioDiscoveryService
from app.ingestion.lazyportfolio.validation import DiscoveryValidationError
from app.repositories.instruments import InstrumentRepository
from tests.ingestion.lazyportfolio.helpers import FixtureClient, remove_symbols


def run_discovery(html: str, settings: Settings, repository: InstrumentRepository) -> DiscoveryRun:
    return LazyPortfolioDiscoveryService(settings, repository, FixtureClient(html)).discover()


def test_first_and_second_import_idempotent(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    first = run_discovery(source_html, settings, repository)
    second = run_discovery(source_html, settings, repository)
    assert first.sync.inserted == 263
    assert second.sync.inserted == 0 and second.sync.updated == 0
    assert second.sync.unchanged == 263
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Instrument)) == 263
        vti = session.scalar(select(Instrument).where(Instrument.source_symbol == "VTI"))
        assert vti.first_seen_at == first.fetched_at.replace(tzinfo=None)
        assert vti.last_seen_at == second.fetched_at.replace(tzinfo=None)
        assert session.get(IngestionState, SOURCE).high_water_count == 263


def test_changed_metadata_updates_existing_record(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    run_discovery(source_html, settings, repository)
    soup = BeautifulSoup(source_html, "lxml")
    for option in soup.select('select.asset-dropdown option[value="QQQ"]'):
        option.string = "Updated Nasdaq label"
    run = run_discovery(str(soup), settings, repository)
    assert run.sync.updated == 1 and run.sync.inserted == 0
    with session_factory() as session:
        row = session.scalar(select(Instrument).where(Instrument.source_symbol == "QQQ"))
        assert row.name == "Updated Nasdaq label"


def test_currency_availability_changes_update_existing_record(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    run_discovery(source_html, settings, repository)
    with session_factory() as session:
        previous = session.scalar(select(Instrument).where(Instrument.source_symbol == "VTI"))
        previous_id = previous.id
    soup = BeautifulSoup(source_html, "lxml")
    for option in soup.select('select.asset-dropdown option[value="VTI"]'):
        option["data-currency"] = "USD,JPY,AUD,CHF,CAD"
    run = run_discovery(str(soup), settings, repository)
    assert run.sync.updated == 1 and run.sync.inserted == 0 and run.sync.deactivated == 0
    with session_factory() as session:
        row = session.scalar(select(Instrument).where(Instrument.source_symbol == "VTI"))
        assert row.id == previous_id
        assert row.currency == "USD"
        assert row.available_currencies == ["USD", "JPY", "AUD", "CHF", "CAD"]


def test_missing_instrument_deactivated_then_reactivated(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    first = run_discovery(source_html, settings, repository)
    second = run_discovery(remove_symbols(source_html, {"QQQ"}), settings, repository)
    assert second.validation.passed and second.sync.deactivated == 1
    with session_factory() as session:
        row = session.scalar(select(Instrument).where(Instrument.source_symbol == "QQQ"))
        assert not row.is_active
        assert row.last_seen_at == first.fetched_at.replace(tzinfo=None)
        assert session.get(IngestionState, SOURCE).high_water_count == 263
    third = run_discovery(source_html, settings, repository)
    assert third.sync.updated == 1 and third.sync.inserted == 0
    with session_factory() as session:
        assert session.scalar(select(Instrument).where(Instrument.source_symbol == "QQQ")).is_active


@pytest.mark.parametrize("failure", ["truncated", "low_count", "conflict", "missing_panel"])
def test_failed_scrape_does_not_modify_existing_universe(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
    failure: str,
) -> None:
    first = run_discovery(source_html, settings, repository)
    soup = BeautifulSoup(source_html, "lxml")
    if failure == "truncated":
        html = source_html[: len(source_html) // 2]
    elif failure == "low_count":
        symbols = {o["value"] for o in soup.select("select.asset-dropdown option[value]")}
        html = remove_symbols(source_html, symbols - {"VTI", "SPY", "TLT", "GLD"})
    elif failure == "conflict":
        soup.select('select.asset-dropdown option[value="QQQ"]')[1].string = "Conflict"
        html = str(soup)
    else:
        for block in soup.select(".asset-block"):
            block.decompose()
        html = str(soup)
    with pytest.raises(DiscoveryValidationError):
        run_discovery(html, settings, repository)
    with session_factory() as session:
        rows = list(session.scalars(select(Instrument)))
        assert len(rows) == 263 and all(row.is_active for row in rows)
        assert all(row.last_seen_at == first.fetched_at.replace(tzinfo=None) for row in rows)
        assert session.get(IngestionState, SOURCE).last_count == 263


def test_historical_guard_prevents_partial_export_and_sync(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    run_discovery(source_html, settings, repository)
    soup = BeautifulSoup(source_html, "lxml")
    symbols = [
        o["value"]
        for o in soup.select("select.asset-dropdown")[0].select("option[value]")
        if o["value"]
    ]
    keep = set(symbols[:100]) | {"VTI", "SPY", "TLT", "GLD"}
    html = remove_symbols(source_html, set(symbols) - keep)
    with pytest.raises(DiscoveryValidationError, match="high-water"):
        LazyPortfolioDiscoveryService(settings, repository, FixtureClient(html)).discover(
            export=True
        )
    assert not settings.export_dir.exists()
    with session_factory() as session:
        assert session.get(IngestionState, SOURCE).last_count == 263
        assert (
            session.scalar(select(func.count()).select_from(Instrument).where(Instrument.is_active))
            == 263
        )


def test_other_sources_are_not_deactivated(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    repository.synchronize(
        "manual",
        [InstrumentRecord(source_symbol="MANUAL", ticker="MANUAL")],
        datetime.now(UTC),
        lambda _: None,
    )
    run_discovery(source_html, settings, repository)
    with session_factory() as session:
        assert session.scalar(select(Instrument).where(Instrument.source == "manual")).is_active


def test_fetch_failure_does_not_touch_database(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    run_discovery(source_html, settings, repository)

    class BrokenClient:
        def fetch_universe(self) -> FetchedPage:
            raise FetchError("HTTP 503")

    with pytest.raises(FetchError):
        LazyPortfolioDiscoveryService(settings, repository, BrokenClient()).discover()
    with session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(Instrument).where(Instrument.is_active))
            == 263
        )


def test_stale_discovery_rejected(
    source_html: str, settings: Settings, repository: InstrumentRepository
) -> None:
    first = run_discovery(source_html, settings, repository)
    page = FixtureClient(source_html).fetch_universe()
    page.fetched_at = first.fetched_at - timedelta(days=1)

    class OldClient:
        def fetch_universe(self) -> FetchedPage:
            return page

    with pytest.raises(IngestionError, match="older discovery"):
        LazyPortfolioDiscoveryService(settings, repository, OldClient()).discover()
