"""Validated, resumable collection by exact catalog instrument and denomination."""

import csv
import hashlib
import json
import logging
import math
import time
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.models import Instrument, MonthlyReturn, ReturnImport, ReturnSeries
from app.ingestion.base import IngestionError
from app.ingestion.lazyportfolio.backtester import (
    BacktesterAdapter,
    BacktesterRestricted,
    PairNotAvailable,
)
from app.ingestion.lazyportfolio.backtester_pilot import run_pilot
from app.ingestion.lazyportfolio.returns_models import ReturnExtractionError, ReturnValidationError
from app.ingestion.lazyportfolio.returns_service import LazyPortfolioReturnsService
from app.ingestion.lazyportfolio.returns_validation import validate_returns
from app.ingestion.lazyportfolio.service import refresh_export_return_statuses
from app.repositories.returns import ReturnRepository

logger = logging.getLogger(__name__)
STATUSES = (
    "completed",
    "pending",
    "partial",
    "failed",
    "not_available_in_backtester",
    "restricted",
)


class ReturnPairService:
    def __init__(
        self,
        settings: Settings,
        repository: ReturnRepository,
        *,
        adapter: BacktesterAdapter | None = None,
        individual: LazyPortfolioReturnsService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.adapter = adapter or BacktesterAdapter(settings)
        self.individual = individual or LazyPortfolioReturnsService(settings, repository)
        self.pairs_path = settings.cache_dir / "backtester_pairs.json"

    def _instruments(self) -> list[Instrument]:
        with self.repository.factory() as session:
            return list(
                session.scalars(
                    select(Instrument)
                    .where(
                        Instrument.source == self.repository.source, Instrument.is_active.is_(True)
                    )
                    .order_by(Instrument.id)
                )
            )

    def _offered(self, *, refresh: bool = False) -> dict:
        if not refresh and self.pairs_path.exists():
            return json.loads(self.pairs_path.read_text(encoding="utf-8"))["instruments"]
        offered = self.adapter.discover_pairs()
        instruments = self._instruments()
        fraction = 1 - self.settings.lazyportfolio_max_drop_fraction
        if len(offered) < math.ceil(len(instruments) * fraction):
            raise ValueError("Backtester selector dropped implausibly relative to the catalog")
        baseline = Counter(c for i in instruments for c in i.available_currencies or [])
        observed = Counter(c for r in offered.values() for c in r["currencies"])
        if any(
            count >= 10 and observed[c] < math.ceil(count * fraction)
            for c, count in baseline.items()
        ):
            raise ValueError(
                "Backtester currency availability dropped implausibly; investigate source"
            )
        self.pairs_path.parent.mkdir(parents=True, exist_ok=True)
        self.pairs_path.write_text(
            json.dumps(
                {"fetched_at": datetime.now(UTC).isoformat(), "instruments": offered}, indent=2
            ),
            encoding="utf-8",
        )
        return offered

    def _pilot_valid(self) -> bool:
        path = self.settings.report_dir / "backtester_pilot_validation.json"
        if not path.exists():
            return False
        report = json.loads(path.read_text(encoding="utf-8"))
        if (
            not report.get("passed")
            or report.get("source_base_url") != self.settings.lazyportfolio_base_url
        ):
            return False
        instruments = {i.source_symbol: i for i in self._instruments()}
        if {p["ticker"] for p in report.get("pilots", [])} != {"VTI", "VTV"}:
            return False
        for pilot in report["pilots"]:
            if not pilot["passed"] or pilot["ticker"] not in instruments:
                return False
            series = self.repository.series(instruments[pilot["ticker"]].id, "USD")
            if series is None:
                return False
            with self.repository.factory() as session:
                observations = session.scalars(
                    select(MonthlyReturn)
                    .where(MonthlyReturn.series_id == series.id)
                    .order_by(MonthlyReturn.date)
                )
                digest = hashlib.sha256(
                    "\n".join(f"{o.date}:{o.return_value}" for o in observations).encode()
                ).hexdigest()
            if digest != pilot["existing_observations_sha256"]:
                return False
        return True

    def _complete(self, series: ReturnSeries | None) -> bool:
        return bool(
            series
            and series.validation_status == "validated"
            and series.source_metadata.get("validation", {}).get("passed")
        )

    def _method_order(
        self,
        instrument: Instrument,
        currency: str,
        series: ReturnSeries | None,
        urls: dict[str, str],
    ) -> list[str]:
        """Reuse a validated pair's method; only native pairs can use detail pages."""
        methods = ["backtester"]
        if currency == instrument.currency and instrument.source_symbol in urls:
            methods.insert(0, "individual_page")
        if self._complete(series) and series.extraction_method in methods:
            methods.remove(series.extraction_method)
            methods.insert(0, series.extraction_method)
        return methods

    def _import_pair(
        self,
        instrument: Instrument,
        currency: str,
        method: str,
        urls: dict[str, str],
        *,
        accept_revisions: bool,
        save_raw: bool | None,
    ) -> str:
        self._coordinate_requests()
        if method == "individual_page":
            status = self.individual.import_one(
                instrument,
                urls[instrument.source_symbol],
                accept_revisions=accept_revisions,
                save_raw=save_raw,
            )
            if status not in {"successful", "completed", "partial"}:
                raise ReturnExtractionError(f"Individual-page import returned {status}")
            return "completed" if status != "partial" else "partial"

        self.repository.checkpoint(
            instrument.id,
            currency,
            "partial",
            {
                "reason": "Extraction started; transaction has not committed",
                "extraction_method": method,
            },
        )
        data = self.adapter.extract(
            instrument.source_symbol,
            currency,
            save_raw=self.settings.returns_save_raw_snapshots if save_raw is None else save_raw,
        )
        validation = validate_returns(data)
        if not validation.passed:
            raise ReturnValidationError(validation)
        result = self.repository.synchronize(instrument.id, data, accept_revisions=accept_revisions)
        status = "completed" if result["accepted"] else "partial"
        self.repository.checkpoint(
            instrument.id,
            currency,
            status,
            {
                "extraction_method": method,
                "url": data.source_url,
                "sync": result,
                "validation_status": "validated",
                "reason": "Validated historical import"
                if result["accepted"]
                else "Source revisions await explicit acceptance",
            },
        )
        return status

    def _coordinate_requests(self) -> None:
        timestamps = [
            value
            for value in (
                getattr(self.adapter, "last_request", None),
                getattr(self.individual, "last_request", None),
            )
            if value is not None
        ]
        if timestamps:
            self.adapter.last_request = self.individual.last_request = max(timestamps)

    def coverage(self, offered: dict | None = None) -> dict:
        if offered is None:
            offered = self._offered()
        instruments = self._instruments()
        with self.repository.factory() as session:
            series = {
                (s.instrument_id, s.currency): s
                for s in session.scalars(
                    select(ReturnSeries).where(
                        ReturnSeries.source == self.repository.source,
                        ReturnSeries.frequency == "monthly",
                        ReturnSeries.return_type == "nominal_total_return",
                    )
                )
            }
            imports = {
                (s.instrument_id, s.currency): s
                for s in session.scalars(
                    select(ReturnImport).where(ReturnImport.source == self.repository.source)
                )
            }
        rows = []
        for instrument in instruments:
            actual = set(offered.get(instrument.source_symbol, {}).get("currencies", []))
            catalog = set(instrument.available_currencies or [])
            if instrument.currency:
                catalog.add(instrument.currency)
            for currency in sorted(actual | catalog | {c for i, c in series if i == instrument.id}):
                stored = series.get((instrument.id, currency))
                checkpoint = imports.get((instrument.id, currency))
                details = checkpoint.details if checkpoint else {}
                status = checkpoint.status if checkpoint else "pending"
                status = {
                    "successful": "completed",
                    "unsupported": "pending",
                    "unresolved": "pending",
                    "not_attempted": "pending",
                }.get(status, status)
                valid = self._complete(stored)
                if valid and status not in {"partial", "failed", "restricted"}:
                    status = "completed"
                if not valid and currency not in actual:
                    status = "not_available_in_backtester"
                    details = {**details, "reason": "Exact pair is absent from current selector"}
                rows.append(
                    {
                        "instrument_id": instrument.id,
                        "ticker": instrument.source_symbol,
                        "currency": currency,
                        "eligible": currency in actual,
                        "catalog_currency": currency in catalog,
                        "status": status,
                        "has_valid_series": valid,
                        "extraction_method": stored.extraction_method
                        if stored
                        else details.get("extraction_method"),
                        "currency_kind": stored.currency_kind
                        if stored
                        else ("native" if currency == instrument.currency else "converted"),
                        "earliest_month": str(stored.start_date) if stored else None,
                        "latest_month": str(stored.end_date) if stored else None,
                        "observation_count": stored.observation_count if stored else 0,
                        "precision_percent_decimals": stored.source_metadata.get(
                            "precision_percent_decimals"
                        )
                        if stored
                        else None,
                        "last_sync": stored.last_updated_at.isoformat() if stored else None,
                        "validation_status": details.get("validation_status")
                        or (stored.validation_status if stored else "not_validated"),
                        "stored_validation_status": stored.validation_status if stored else None,
                        "attempted_at": checkpoint.attempted_at.isoformat() if checkpoint else None,
                        "reason": details.get(
                            "reason",
                            "Validated observations already stored"
                            if valid
                            else "Not yet attempted",
                        ),
                    }
                )
        eligible = [r for r in rows if r["eligible"]]
        completed = [r for r in eligible if r["has_valid_series"]]
        counts = Counter(r["status"] for r in eligible)
        result = {
            "generated_at": datetime.now(UTC).isoformat(),
            "catalog_count": len(instruments),
            "eligible_pair_count": len(eligible),
            "completed_pairs": len(completed),
            "individual_page_pairs": sum(
                r["extraction_method"] == "individual_page" for r in completed
            ),
            "backtester_pairs": sum(r["extraction_method"] == "backtester" for r in completed),
            "missing_pairs": len(eligible) - len(completed),
            "failed_validation_pairs": sum(r["validation_status"] == "failed" for r in eligible),
            "attempted_eligible_pairs": sum(bool(r["attempted_at"]) for r in eligible),
            "status_counts": {s: counts.get(s, 0) for s in STATUSES},
            "pairs": rows,
        }
        self.settings.report_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.report_dir / "lazyportfolio_returns_coverage.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        for name, records in (
            ("lazyportfolio_returns_coverage.csv", rows),
            (
                "lazyportfolio_returns_unresolved.csv",
                [r for r in rows if not r["has_valid_series"]],
            ),
        ):
            with (self.settings.report_dir / name).open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else [])
                writer.writeheader()
                writer.writerows(records)
        refresh_export_return_statuses(
            self.settings.export_dir / "lazyportfolio_instruments.csv",
            self.repository.catalog_statuses(),
        )
        return result

    def run(
        self,
        *,
        symbol: str | None = None,
        all_active: bool = False,
        currency: str | None = None,
        update: bool = False,
        accept_revisions: bool = False,
        save_raw: bool | None = None,
        refresh_index: bool = False,
    ) -> dict:
        self.settings.report_dir.mkdir(parents=True, exist_ok=True)
        if refresh_index:
            raise ValueError("URL crawling is disabled; no --refresh-index is needed")
        if not self._pilot_valid():
            pilot = run_pilot(self.adapter, self.repository)
            if not pilot["passed"]:
                raise ValueError("Backtester pilot failed; bulk collection is prohibited")
        offered = self._offered(refresh=True)
        instruments = [i for i in self._instruments() if all_active or i.source_symbol == symbol]
        if not instruments:
            raise ValueError("No matching active catalog instruments")
        urls = self.individual.public_page_index()
        stopped = None
        selected = []
        for instrument in instruments:
            actual = set(offered.get(instrument.source_symbol, {}).get("currencies", []))
            currencies = (
                (
                    {currency.upper()}
                    if currency
                    else actual | set(instrument.available_currencies or [])
                )
                if all_active or currency
                else {instrument.currency}
            )
            for denomination in sorted(c for c in currencies if c):
                selected.append((instrument.source_symbol, denomination))
                series = self.repository.series(instrument.id, denomination)
                if self._complete(series) and not update:
                    previous = self.repository.status(instrument.id, denomination)
                    if not previous or previous.status != "completed":
                        self.repository.checkpoint(
                            instrument.id,
                            denomination,
                            "completed",
                            {
                                "reason": "Existing validated series preserved",
                                "extraction_method": series.extraction_method,
                            },
                        )
                    continue
                if denomination not in actual:
                    self.repository.checkpoint(
                        instrument.id,
                        denomination,
                        "not_available_in_backtester",
                        {"reason": "Exact ticker–currency pair absent from current selector"},
                    )
                    continue
                methods = self._method_order(instrument, denomination, series, urls)
                method = methods[0]
                attempts = []
                logger.info(
                    "lazyportfolio.returns.method.selected symbol=%s currency=%s "
                    "method=%s reason=%s fallback=%s",
                    instrument.source_symbol,
                    denomination,
                    method,
                    "previous_validated_method"
                    if self._complete(series) and method == series.extraction_method
                    else "default_route",
                    methods[1:],
                )
                try:
                    for index, method in enumerate(methods):
                        started = time.perf_counter()
                        try:
                            status = self._import_pair(
                                instrument,
                                denomination,
                                method,
                                urls,
                                accept_revisions=accept_revisions,
                                save_raw=save_raw,
                            )
                        except (IngestionError, ValueError) as exc:
                            restricted = isinstance(exc, BacktesterRestricted) or any(
                                code in str(exc)
                                for code in ("HTTP 401", "HTTP 403", "HTTP 429", "Retry-After")
                            )
                            attempts.append(
                                {
                                    "method": method,
                                    "status": "restricted" if restricted else "failed",
                                    "reason": str(exc),
                                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                                }
                            )
                            if restricted:
                                raise BacktesterRestricted(str(exc)) from exc
                            if index + 1 == len(methods):
                                raise
                            logger.warning(
                                "lazyportfolio.returns.method.fallback symbol=%s currency=%s "
                                "failed_method=%s next_method=%s reason=%s",
                                instrument.source_symbol,
                                denomination,
                                method,
                                methods[index + 1],
                                exc,
                            )
                            continue
                        attempts.append(
                            {
                                "method": method,
                                "status": status,
                                "elapsed_seconds": round(time.perf_counter() - started, 3),
                            }
                        )
                        old = self.repository.status(instrument.id, denomination)
                        self.repository.checkpoint(
                            instrument.id,
                            denomination,
                            status,
                            {
                                **(old.details if old else {}),
                                "extraction_method": method,
                                "method_attempts": attempts,
                            },
                        )
                        logger.info(
                            "lazyportfolio.returns.method.completed symbol=%s currency=%s "
                            "method=%s status=%s elapsed_seconds=%s",
                            instrument.source_symbol,
                            denomination,
                            method,
                            status,
                            attempts[-1]["elapsed_seconds"],
                        )
                        print(
                            f"{instrument.source_symbol} {denomination}: {status} ({method})",
                            flush=True,
                        )
                        break
                except KeyboardInterrupt:
                    attempts.append(
                        {
                            "method": method,
                            "status": "partial",
                            "reason": "Interrupted; rerun to resume",
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                        }
                    )
                    self.repository.checkpoint(
                        instrument.id,
                        denomination,
                        "partial",
                        {
                            "reason": "Interrupted; rerun to resume",
                            "extraction_method": method,
                            "method_attempts": attempts,
                        },
                    )
                    self.coverage(offered)
                    raise
                except (IngestionError, ValueError, OSError, SQLAlchemyError) as exc:
                    status = (
                        "restricted"
                        if isinstance(exc, BacktesterRestricted)
                        else (
                            "not_available_in_backtester"
                            if isinstance(exc, PairNotAvailable)
                            else "failed"
                        )
                    )
                    if not attempts or attempts[-1]["method"] != method:
                        attempts.append(
                            {
                                "method": method,
                                "status": status,
                                "reason": str(exc),
                                "elapsed_seconds": round(time.perf_counter() - started, 3),
                            }
                        )
                    self.repository.checkpoint(
                        instrument.id,
                        denomination,
                        status,
                        {
                            "reason": str(exc),
                            "extraction_method": method,
                            "method_attempts": attempts,
                            "validation_status": "failed"
                            if isinstance(exc, ReturnValidationError)
                            else "not_validated",
                        },
                    )
                    print(f"{instrument.source_symbol} {denomination}: {status}: {exc}", flush=True)
                    if status == "restricted":
                        stopped = str(exc)
                        break
            self.coverage(offered)
            if stopped:
                break
        report = self.coverage(offered)
        report["batch_stopped"] = stopped
        report["selected_symbols"] = [i.source_symbol for i in instruments]
        report["selected_pairs"] = selected
        (self.settings.report_dir / "lazyportfolio_returns_coverage.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report
