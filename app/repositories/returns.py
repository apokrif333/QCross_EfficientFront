import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Instrument, MonthlyReturn, ReturnImport, ReturnRevision, ReturnSeries
from app.ingestion.returns import ValidatedReturnData

logger = logging.getLogger(__name__)


class ReturnRepository:
    def __init__(self, factory: sessionmaker[Session], *, source: str) -> None:
        self.factory = factory
        self.source = source

    def series(self, instrument_id: int, currency: str) -> ReturnSeries | None:
        with self.factory() as session:
            return session.scalar(
                select(ReturnSeries).where(
                    ReturnSeries.instrument_id == instrument_id,
                    ReturnSeries.source == self.source,
                    ReturnSeries.currency == currency,
                    ReturnSeries.frequency == "monthly",
                    ReturnSeries.return_type == "nominal_total_return",
                )
            )

    def checkpoint(
        self, instrument_id: int, currency: str, status: str, details: dict[str, Any]
    ) -> None:
        with self.factory.begin() as session:
            row = session.scalar(
                select(ReturnImport).where(
                    ReturnImport.instrument_id == instrument_id,
                    ReturnImport.source == self.source,
                    ReturnImport.currency == currency,
                )
            )
            if row is None:
                row = ReturnImport(
                    instrument_id=instrument_id, source=self.source, currency=currency
                )
                session.add(row)
            row.status = status
            row.details = details
            row.attempted_at = datetime.now(UTC)

    def status(self, instrument_id: int, currency: str) -> ReturnImport | None:
        with self.factory() as session:
            return session.scalar(
                select(ReturnImport).where(
                    ReturnImport.instrument_id == instrument_id,
                    ReturnImport.source == self.source,
                    ReturnImport.currency == currency,
                )
            )

    def catalog_statuses(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Return import status and stored-observation counts by catalog symbol/currency."""
        with self.factory() as session:
            rows = session.execute(
                select(
                    Instrument.source_symbol,
                    Instrument.currency,
                    ReturnImport.status,
                    ReturnSeries.observation_count,
                )
                .outerjoin(
                    ReturnImport,
                    and_(
                        ReturnImport.instrument_id == Instrument.id,
                        ReturnImport.source == self.source,
                        ReturnImport.currency == Instrument.currency,
                    ),
                )
                .outerjoin(
                    ReturnSeries,
                    and_(
                        ReturnSeries.instrument_id == Instrument.id,
                        ReturnSeries.source == self.source,
                        ReturnSeries.currency == Instrument.currency,
                        ReturnSeries.frequency == "monthly",
                        ReturnSeries.return_type == "nominal_total_return",
                    ),
                )
                .where(Instrument.source == self.source)
            ).all()
        return {
            (symbol, currency or "UNK"): {
                "returns_status": status or "not_attempted",
                "returns_downloaded": bool(observation_count),
                "return_observation_count": observation_count or 0,
            }
            for symbol, currency, status, observation_count in rows
        }

    def synchronize(
        self, instrument_id: int, data: ValidatedReturnData, *, accept_revisions: bool = False
    ) -> dict[str, Any]:
        # Revalidate at the write boundary: callers cannot pass an arbitrary "PASS" boolean.
        data.require_valid()
        storage_metadata = data.storage_metadata
        if data.source != self.source:
            raise ValueError("Return source does not match repository provider")
        with self.factory.begin() as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            # Lock the instrument too, including first import before a series exists.
            from app.db.models import Instrument

            instrument = session.scalar(
                select(Instrument).where(Instrument.id == instrument_id).with_for_update()
            )
            if instrument is None or instrument.source_symbol != data.source_symbol:
                raise ValueError("Source identity/currency does not match the existing catalog")
            series = session.scalar(
                select(ReturnSeries)
                .where(
                    ReturnSeries.instrument_id == instrument_id,
                    ReturnSeries.source == self.source,
                    ReturnSeries.currency == data.currency,
                    ReturnSeries.frequency == "monthly",
                    ReturnSeries.return_type == "nominal_total_return",
                )
                .with_for_update()
            )
            if series is None:
                series = ReturnSeries(
                    instrument_id=instrument_id, source=self.source, currency=data.currency
                )
                # Required non-null fields must be populated before flush for the series FK.
                series.start_date = data.start_date
                series.end_date = data.end_date
                series.observation_count = len(data.observations)
                series.last_updated_at = data.extracted_at
                series.source_metadata = {}
                session.add(series)
                session.flush()
            else:
                updated = series.last_updated_at
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                if data.extracted_at < updated:
                    raise ValueError("Refusing a stale extraction")
                if data.start_date > series.start_date or data.end_date < series.end_date:
                    raise ValueError("Refusing to shorten the previously imported history")
            stored = {
                r.date: r
                for r in session.scalars(
                    select(MonthlyReturn).where(MonthlyReturn.series_id == series.id)
                )
            }
            detected = []
            for observation in data.observations:
                old = stored.get(observation.date)
                if old is not None and old.return_value != observation.return_value:
                    revision = session.scalar(
                        select(ReturnRevision).where(
                            ReturnRevision.series_id == series.id,
                            ReturnRevision.date == observation.date,
                            ReturnRevision.old_value == old.return_value,
                            ReturnRevision.new_value == observation.return_value,
                        )
                    )
                    if revision is None:
                        revision = ReturnRevision(
                            series_id=series.id,
                            date=observation.date,
                            old_value=old.return_value,
                            new_value=observation.return_value,
                        )
                        session.add(revision)
                    if accept_revisions:
                        revision.applied_at = data.extracted_at
                    detected.append(revision)
                    logger.warning(
                        "returns.revision symbol=%s date=%s old=%s new=%s applied=%s",
                        data.source_symbol,
                        observation.date,
                        old.return_value,
                        observation.return_value,
                        accept_revisions,
                    )
            if detected and not accept_revisions:
                return {
                    "accepted": False,
                    "revisions": len(detected),
                    "inserted": 0,
                    "updated": 0,
                    "reason": "Revisions recorded; use --accept-revisions",
                }
            inserted = updated_count = 0
            for observation in data.observations:
                old = stored.get(observation.date)
                metadata = dict(
                    observation.source_metadata,
                    extracted_at=data.extracted_at.isoformat(),
                    source_url=data.source_url,
                )
                if old is None:
                    session.add(
                        MonthlyReturn(
                            series_id=series.id,
                            date=observation.date,
                            return_value=observation.return_value,
                            quality_flag=observation.quality_flag,
                            source_metadata=metadata,
                        )
                    )
                    inserted += 1
                elif (
                    old.return_value != observation.return_value
                    or old.quality_flag != observation.quality_flag
                ):
                    old.return_value = observation.return_value
                    old.quality_flag = observation.quality_flag
                    old.source_metadata = metadata
                    old.updated_at = datetime.now(UTC)
                    updated_count += 1
            series.start_date = data.start_date
            series.end_date = data.end_date
            series.observation_count = len(data.observations)
            series.last_updated_at = data.extracted_at
            series.is_extended_history = data.is_extended_history
            series.source_metadata = storage_metadata
            series.extraction_method = storage_metadata.get("extraction_method", "individual_page")
            series.currency_kind = storage_metadata.get("currency_kind", "native")
            series.validation_status = "validated"
            result = {
                "accepted": True,
                "inserted": inserted,
                "updated": updated_count,
                "unchanged": len(data.observations) - inserted - updated_count,
                "revisions": len(detected),
                "series_id": series.id,
            }
            logger.info(
                "returns.sync.completed symbol=%s stats=%s",
                data.source_symbol,
                result,
            )
            return result
