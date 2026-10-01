import csv
import json

import pytest

from app.config import Settings
from app.ingestion.lazyportfolio.service import (
    LazyPortfolioDiscoveryService,
    refresh_export_return_statuses,
)
from app.ingestion.lazyportfolio.validation import DiscoveryValidationError
from app.storage.snapshots import read_snapshot
from tests.ingestion.lazyportfolio.helpers import FixtureClient


def test_exports_and_raw_snapshot(source_html: str, settings: Settings) -> None:
    run = LazyPortfolioDiscoveryService(settings, client=FixtureClient(source_html)).discover(
        save_raw=True,
        export=True,
    )
    assert run.sync is None
    assert read_snapshot(run.snapshot_path, settings.archive_dir) == source_html.encode()
    with (settings.export_dir / "lazyportfolio_instruments.csv").open(
        newline="", encoding="utf-8"
    ) as f:
        rows = list(csv.DictReader(f))
    payload = json.loads(
        (settings.export_dir / "lazyportfolio_instruments.json").read_text(encoding="utf-8")
    )
    assert len(rows) == len(payload) == 263
    assert next(r for r in rows if r["source_symbol"] == "^BTC")["is_active"] == "True"
    assert next(r for r in rows if r["source_symbol"] == "VTI")["returns_status"] == (
        "not_attempted"
    )
    assert next(r for r in rows if r["source_symbol"] == "VTI")["returns_downloaded"] == "False"
    assert next(r for r in payload if r["source_symbol"] == "VTI")["currency"] == "USD"
    assert next(r for r in rows if r["source_symbol"] == "VTI")["currency"] == "USD"
    assert next(r for r in rows if r["source_symbol"] == "VTI")["available_currencies"] == (
        "USD|JPY|AUD|CHF"
    )
    assert next(r for r in payload if r["source_symbol"] == "VTI")["available_currencies"] == [
        "USD",
        "JPY",
        "AUD",
        "CHF",
    ]
    same_name = [r for r in rows if r["name"] == "US Total Stock Market"]
    assert {r["source_symbol"]: r["currency"] for r in same_name} == {
        "VTI": "USD",
        "VUN.TO": "CAD",
        "XD9U.DE": "EUR",
        "XDUS.L": "GBP",
    }
    assert all(i["country"] is None for i in payload)
    assert (settings.report_dir / "lazyportfolio_discovery_report.json").exists()


def test_export_includes_return_download_status(source_html: str, settings: Settings) -> None:
    class StatusProvider:
        def catalog_statuses(self):
            return {
                ("VTI", "USD"): {
                    "returns_status": "successful",
                    "returns_downloaded": True,
                    "return_observation_count": 2804,
                },
                ("SPY", "USD"): {
                    "returns_status": "unsupported",
                    "returns_downloaded": False,
                    "return_observation_count": 0,
                },
            }

    LazyPortfolioDiscoveryService(
        settings,
        client=FixtureClient(source_html),
        return_status_provider=StatusProvider(),
    ).discover(export=True)
    with (settings.export_dir / "lazyportfolio_instruments.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = {row["source_symbol"]: row for row in csv.DictReader(handle)}
    assert rows["VTI"]["returns_status"] == "successful"
    assert rows["VTI"]["returns_downloaded"] == "True"
    assert rows["VTI"]["return_observation_count"] == "2804"
    assert rows["SPY"]["returns_downloaded"] == "False"

    refresh_export_return_statuses(
        settings.export_dir / "lazyportfolio_instruments.csv",
        {
            ("VTI", "USD"): {
                "returns_status": "partial",
                "returns_downloaded": True,
                "return_observation_count": 12,
            }
        },
    )
    with (settings.export_dir / "lazyportfolio_instruments.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        refreshed = {row["source_symbol"]: row for row in csv.DictReader(handle)}
    assert refreshed["VTI"]["returns_status"] == "partial"
    assert refreshed["VTI"]["returns_downloaded"] == "True"
    assert refreshed["SPY"]["returns_status"] == "not_attempted"


def test_invalid_discovery_never_overwrites_exports(source_html: str, settings: Settings) -> None:
    LazyPortfolioDiscoveryService(settings, client=FixtureClient(source_html)).discover(export=True)
    path = settings.export_dir / "lazyportfolio_instruments.json"
    previous = path.read_bytes()
    with pytest.raises(DiscoveryValidationError):
        LazyPortfolioDiscoveryService(settings, client=FixtureClient("<html>")).discover(
            export=True
        )
    assert path.read_bytes() == previous
