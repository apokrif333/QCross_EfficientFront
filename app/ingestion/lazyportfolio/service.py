import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.config import Settings
from app.ingestion.base import CompletenessBaseline, InstrumentRecord, SyncStats, UniverseRepository
from app.ingestion.lazyportfolio.client import FetchedPage, LazyPortfolioClient
from app.ingestion.lazyportfolio.models import ParseResult, ValidationReport
from app.ingestion.lazyportfolio.parser import parse_instruments
from app.ingestion.lazyportfolio.validation import validate_discovery
from app.storage.snapshots import save_snapshot

logger = logging.getLogger(__name__)
SOURCE = "lazyportfolioetf"


class SourceClient(Protocol):
    def fetch_universe(self) -> FetchedPage: ...


class ReturnStatusProvider(Protocol):
    def catalog_statuses(self) -> dict[tuple[str, str], dict[str, Any]]: ...


class DiscoveryRun(BaseModel):
    source: str = SOURCE
    fetched_at: datetime
    source_url: str
    parsed: ParseResult
    validation: ValidationReport
    sync: SyncStats | None = None
    snapshot_path: Path | None = None
    export_paths: list[Path] = Field(default_factory=list)


def normalize_instruments(result: ParseResult, source_url: str) -> list[InstrumentRecord]:
    return [
        InstrumentRecord(
            source_symbol=item.source_symbol,
            ticker=item.source_symbol,
            name=item.name,
            category=item.category,
            currency=item.currency,
            available_currencies=item.available_currencies,
            source_url=source_url,
            raw_metadata=item.source_metadata,
        )
        for item in result.instruments
    ]


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def refresh_export_return_statuses(
    path: Path,
    return_statuses: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Refresh return-import columns in an existing instrument CSV without refetching."""
    if not path.is_file():
        return
    from io import StringIO

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    status_fields = ["returns_status", "returns_downloaded", "return_observation_count"]
    for field in status_fields:
        if field not in fieldnames:
            fieldnames.append(field)
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        key = (row.get("source_symbol", ""), row.get("currency") or "UNK")
        row.update(
            return_statuses.get(
                key,
                {
                    "returns_status": "not_attempted",
                    "returns_downloaded": False,
                    "return_observation_count": 0,
                },
            )
        )
        writer.writerow(row)
    try:
        _atomic_write(path, output.getvalue())
    except PermissionError:
        pending = path.with_name(path.stem + ".updated" + path.suffix)
        _atomic_write(pending, output.getvalue())
        logger.warning("catalog.export.locked path=%s updated_copy=%s", path, pending)


def export_instruments(
    records: list[InstrumentRecord],
    fetched_at: datetime,
    directory: Path,
    return_statuses: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[Path]:
    """Exports describe the successful current discovery, with all entries active."""
    from io import StringIO

    csv_buffer = StringIO(newline="")
    writer = csv.DictWriter(
        csv_buffer,
        fieldnames=[
            "source_symbol",
            "name",
            "category",
            "currency",
            "available_currencies",
            "is_active",
            "returns_status",
            "returns_downloaded",
            "return_observation_count",
        ],
    )
    writer.writeheader()
    for item in records:
        status = (return_statuses or {}).get(
            (item.source_symbol, item.currency or "UNK"),
            {
                "returns_status": "not_attempted",
                "returns_downloaded": False,
                "return_observation_count": 0,
            },
        )
        writer.writerow(
            {
                "source_symbol": item.source_symbol,
                "name": item.name,
                "category": item.category,
                "currency": item.currency,
                "available_currencies": "|".join(item.available_currencies or []),
                "is_active": True,
                **status,
            }
        )
    csv_path = directory / "lazyportfolio_instruments.csv"
    json_path = directory / "lazyportfolio_instruments.json"
    _atomic_write(csv_path, csv_buffer.getvalue())
    _atomic_write(
        json_path,
        json.dumps(
            [
                dict(
                    item.model_dump(mode="json"),
                    source=SOURCE,
                    is_active=True,
                    discovered_at=fetched_at.isoformat(),
                )
                for item in records
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return [csv_path, json_path]


class LazyPortfolioDiscoveryService:
    def __init__(
        self,
        settings: Settings,
        repository: UniverseRepository | None = None,
        client: SourceClient | None = None,
        return_status_provider: ReturnStatusProvider | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.client = client or LazyPortfolioClient(settings)
        self.return_status_provider = return_status_provider

    def discover(self, *, save_raw: bool | None = None, export: bool = False) -> DiscoveryRun:
        page = self.client.fetch_universe()
        snapshot_path = None
        should_save_raw = self.settings.save_raw_snapshots if save_raw is None else save_raw
        if should_save_raw:
            snapshot_path = save_snapshot(
                self.settings.raw_snapshot_dir / "catalog",
                page.content,
                symbol="instrument_catalog",
                currency=None,
                method="selector",
                captured_at=page.fetched_at,
            )
        result = parse_instruments(page.html)
        report = validate_discovery(result, self.settings)
        records = normalize_instruments(result, page.url)
        sync = None
        if self.repository is not None:

            def validate_against_history(baseline: CompletenessBaseline) -> None:
                nonlocal report
                report = validate_discovery(result, self.settings, baseline)

            sync = self.repository.synchronize(
                SOURCE, records, page.fetched_at, validate_against_history
            )
            logger.info(
                "lazyportfolio.sync.completed inserted=%d updated=%d unchanged=%d deactivated=%d",
                sync.inserted,
                sync.updated,
                sync.unchanged,
                sync.deactivated,
            )
        return_statuses = (
            self.return_status_provider.catalog_statuses()
            if export and self.return_status_provider is not None
            else None
        )
        paths = (
            export_instruments(
                records,
                page.fetched_at,
                self.settings.export_dir,
                return_statuses=return_statuses,
            )
            if export
            else []
        )
        run = DiscoveryRun(
            fetched_at=page.fetched_at,
            source_url=page.url,
            parsed=result,
            validation=report,
            sync=sync,
            snapshot_path=snapshot_path,
            export_paths=paths,
        )
        if export:
            _atomic_write(
                self.settings.report_dir / "lazyportfolio_discovery_report.json",
                run.model_dump_json(indent=2) + "\n",
            )
        return run
