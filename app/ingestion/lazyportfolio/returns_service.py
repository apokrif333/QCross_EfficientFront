import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import select

from app.config import Settings
from app.db.models import Instrument, ReturnSeries
from app.ingestion.base import IngestionError
from app.ingestion.lazyportfolio.client import FetchedPage, LazyPortfolioClient
from app.ingestion.lazyportfolio.returns_models import ReturnExtractionError, ReturnValidationError
from app.ingestion.lazyportfolio.returns_parser import parse_returns
from app.ingestion.lazyportfolio.returns_validation import validate_returns
from app.ingestion.lazyportfolio.service import refresh_export_return_statuses
from app.repositories.returns import ReturnRepository
from app.storage.snapshots import save_snapshot

logger = logging.getLogger(__name__)
VTI_PATH = "/etf/vanguard-total-stock-market-vti/"


class LazyPortfolioReturnsService:
    def __init__(
        self,
        settings: Settings,
        repository: ReturnRepository,
        *,
        client: LazyPortfolioClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.client = client or LazyPortfolioClient(
            settings.model_copy(
                update={
                    "http_backoff_seconds": max(
                        settings.http_backoff_seconds, settings.returns_request_interval
                    ),
                }
            )
        )
        self.sleep = sleep
        self.last_request: float | None = None
        self.index_path = settings.cache_dir / "individual_pages.json"

    def fetch(self, path: str, *, media_types: tuple[str, ...] | None = None) -> FetchedPage:
        if self.last_request is not None:
            self.sleep(
                max(
                    0,
                    self.settings.returns_request_interval - (time.monotonic() - self.last_request),
                )
            )
        try:
            if media_types is not None:
                return self.client.fetch_document(path, media_types=media_types)
            return self.client.fetch_page(path)
        finally:
            self.last_request = time.monotonic()

    def public_page_index(self, *, refresh: bool = False) -> dict[str, str]:
        """Read known URLs only. Network URL discovery was retired in Task 2.1."""
        if refresh:
            raise ReturnExtractionError("URL crawling is disabled; use the backtester")
        document = (
            json.loads(self.index_path.read_text(encoding="utf-8"))
            if self.index_path.exists()
            else {}
        )
        urls = dict(document.get("urls", {}))
        with self.repository.factory() as session:
            rows = session.execute(
                select(ReturnSeries, Instrument)
                .join(Instrument, Instrument.id == ReturnSeries.instrument_id)
                .where(ReturnSeries.source == self.repository.source)
            ).all()
        for series, instrument in rows:
            if series.extraction_method == "individual_page" and series.source_metadata.get(
                "source_url"
            ):
                urls[instrument.source_symbol] = series.source_metadata["source_url"]
        urls.setdefault("VTI", self.settings.lazyportfolio_base_url.rstrip("/") + VTI_PATH)
        return urls

    def resolve_public_links(self) -> dict:
        raise ReturnExtractionError("Site-wide URL discovery is disabled; use the backtester")

    def import_one(
        self,
        instrument: Instrument,
        url: str | None,
        *,
        accept_revisions: bool = False,
        save_raw: bool | None = None,
    ) -> str:
        currency = instrument.currency or "UNK"
        if url is None or instrument.currency is None:
            status = "unresolved" if url is None else "unsupported"
            self.repository.checkpoint(
                instrument.id,
                currency,
                status,
                {
                    "reason": (
                        "No detail URL resolved by the public-link index; "
                        "history availability unknown"
                    )
                    if url is None
                    else "Native currency is not supplied by the catalog",
                },
            )
            return status
        self.repository.checkpoint(
            instrument.id,
            currency,
            "partial",
            {
                "reason": "Import started; series write has not committed",
                "url": url,
            },
        )
        page = self.fetch(url)
        if page.url.rstrip("/").endswith("-dividend-yield"):
            candidates = {
                a["href"]
                for a in BeautifulSoup(page.html, "lxml").select("a[href]")
                if "Historical Returns" in a.get_text(" ", strip=True)
                and urlsplit(a["href"]).netloc == urlsplit(page.url).netloc
            }
            if len(candidates) != 1:
                raise ReturnExtractionError(
                    "Dividend page lacks one explicit historical-return link"
                )
            page = self.fetch(candidates.pop())
        snapshot = None
        if self.settings.returns_save_raw_snapshots if save_raw is None else save_raw:
            snapshot = save_snapshot(
                self.settings.raw_snapshot_dir / "individual_pages",
                page.content,
                symbol=instrument.source_symbol,
                currency=currency,
                method="individual_page",
                captured_at=page.fetched_at,
            )
        if urlsplit(page.url).path.rstrip("/") == "/etf":
            raise ReturnExtractionError(
                f"Published detail link redirected to ETF directory; no instrument data at {url}"
            )
        data = parse_returns(
            page.html,
            symbol=instrument.source_symbol,
            currency=currency,
            source_url=page.url,
            extracted_at=page.fetched_at,
        )
        data.snapshot_path = str(snapshot) if snapshot else None
        data.snapshot_sha256 = hashlib.sha256(page.content).hexdigest()
        data.snapshot_encoding = page.encoding
        report = validate_returns(data)
        if not report.passed:
            self.repository.checkpoint(
                instrument.id,
                currency,
                "partial",
                {
                    "reason": "Extracted data failed validation",
                    "errors": report.errors,
                    "url": url,
                },
            )
            raise ReturnValidationError(report)
        result = self.repository.synchronize(instrument.id, data, accept_revisions=accept_revisions)
        status = "successful" if result["accepted"] else "partial"
        self.repository.checkpoint(
            instrument.id,
            currency,
            status,
            {
                "url": url,
                "resolved_url": page.url,
                "validation_status": "validated",
                "sync": result,
                "warnings": report.warnings,
            },
        )
        return status

    def run(
        self,
        *,
        symbol: str | None = None,
        all_active: bool = False,
        update: bool = False,
        accept_revisions: bool = False,
        save_raw: bool | None = None,
        refresh_index: bool = False,
    ) -> dict:
        with self.repository.factory() as session:
            query = select(Instrument).where(Instrument.source == "lazyportfolioetf")
            if all_active:
                query = query.where(Instrument.is_active.is_(True))
            else:
                query = query.where(Instrument.source_symbol == symbol)
            instruments = list(session.scalars(query.order_by(Instrument.id)))
        if not instruments:
            raise ValueError("No matching instruments in catalog; run instrument discovery first")
        if all_active:
            with self.repository.factory() as session:
                pilot = session.scalar(
                    select(Instrument).where(
                        Instrument.source == "lazyportfolioetf", Instrument.source_symbol == "VTI"
                    )
                )
            series = self.repository.series(pilot.id, "USD") if pilot else None
            if series is None or not series.source_metadata.get("validation", {}).get("passed"):
                raise ReturnExtractionError(
                    "Bulk collection requires a successfully validated VTI pilot"
                )
        needs_index = any(
            i.source_symbol != "VTI"
            and (
                update
                or self.repository.series(i.id, i.currency or "UNK") is None
                or self.repository.status(i.id, i.currency or "UNK") is None
                or self.repository.status(i.id, i.currency or "UNK").status != "successful"
            )
            for i in instruments
        )
        try:
            urls = (
                self.public_page_index(refresh=refresh_index)
                if needs_index
                else {
                    "VTI": self.settings.lazyportfolio_base_url.rstrip("/") + VTI_PATH,
                }
            )
        except IngestionError as exc:
            result = self.coverage()
            result["batch_stopped"] = "Public page index unavailable: " + str(exc)
            result["selected_symbols"] = [i.source_symbol for i in instruments]
            (self.settings.export_dir / "lazyportfolio_returns_coverage.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            return result
        stopped = None
        index_document = (
            json.loads(self.index_path.read_text(encoding="utf-8"))
            if self.index_path.exists()
            else {}
        )
        for instrument in instruments:
            currency = instrument.currency or "UNK"
            previous = self.repository.status(instrument.id, currency)
            if (
                not update
                and previous
                and previous.status == "successful"
                and self.repository.series(instrument.id, currency) is not None
            ):
                logger.info("lazyportfolio.returns.skip symbol=%s", instrument.source_symbol)
                continue
            try:
                targets = list(
                    dict.fromkeys(
                        [
                            urls.get(instrument.source_symbol),
                            *index_document.get("aliases", {}).get(instrument.source_symbol, []),
                        ]
                    )
                )
                for position, target in enumerate(targets):
                    try:
                        status = self.import_one(
                            instrument,
                            target,
                            accept_revisions=accept_revisions,
                            save_raw=save_raw,
                        )
                        break
                    except ReturnExtractionError:
                        if position + 1 == len(targets):
                            raise
                        logger.warning(
                            "lazyportfolio.returns.link.invalid symbol=%s url=%s; "
                            "checking published alternative",
                            instrument.source_symbol,
                            target,
                        )
                print(f"{instrument.source_symbol} {currency}: {status}", flush=True)
            except KeyboardInterrupt:
                self.repository.checkpoint(
                    instrument.id,
                    currency,
                    "partial",
                    {
                        "reason": "Interrupted; retry resumes this instrument",
                    },
                )
                self.coverage()
                raise
            except (IngestionError, ValueError, OSError) as exc:
                status = "partial" if isinstance(exc, ReturnValidationError) else "failed"
                details = {"reason": str(exc), "url": urls.get(instrument.source_symbol)}
                if isinstance(exc, ReturnValidationError):
                    details["errors"] = exc.report.errors
                self.repository.checkpoint(instrument.id, currency, status, details)
                logger.error(
                    "lazyportfolio.returns.import.failed symbol=%s reason=%s",
                    instrument.source_symbol,
                    exc,
                )
                print(f"{instrument.source_symbol} {currency}: {status}: {exc}", flush=True)
                if any(
                    code in str(exc) for code in ("HTTP 401", "HTTP 403", "HTTP 429", "Retry-After")
                ):
                    stopped = "Source requested reduced access; batch stopped: " + str(exc)
                    break
        result = self.coverage()
        result["batch_stopped"] = stopped
        result["selected_symbols"] = [i.source_symbol for i in instruments]
        path = self.settings.report_dir / "lazyportfolio_returns_coverage.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    def coverage(self) -> dict:
        rows = []
        with self.repository.factory() as session:
            instruments = list(
                session.scalars(
                    select(Instrument)
                    .where(Instrument.source == "lazyportfolioetf", Instrument.is_active.is_(True))
                    .order_by(Instrument.id)
                )
            )
        for instrument in instruments:
            currency = instrument.currency or "UNK"
            checkpoint = self.repository.status(instrument.id, currency)
            series = self.repository.series(instrument.id, currency)
            rows.append(
                {
                    "instrument_id": instrument.id,
                    "ticker": instrument.source_symbol,
                    "currency": currency,
                    "status": checkpoint.status if checkpoint else "not_attempted",
                    "earliest_month": str(series.start_date) if series else None,
                    "latest_month": str(series.end_date) if series else None,
                    "observation_count": series.observation_count if series else 0,
                    "precision_percent_decimals": series.source_metadata.get(
                        "precision_percent_decimals"
                    )
                    if series
                    else None,
                    "details": checkpoint.details if checkpoint else {},
                }
            )
        counts = Counter(r["status"] for r in rows)
        result = {
            "generated_at": datetime.now(UTC).isoformat(),
            "catalog_count": len(rows),
            "status_counts": {
                status: counts.get(status, 0)
                for status in (
                    "successful",
                    "partial",
                    "unsupported",
                    "unresolved",
                    "failed",
                    "not_attempted",
                )
            },
            "instruments": rows,
        }
        self.settings.report_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.report_dir / "lazyportfolio_returns_coverage.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        refresh_export_return_statuses(
            self.settings.export_dir / "lazyportfolio_instruments.csv",
            self.repository.catalog_statuses(),
        )
        return result
