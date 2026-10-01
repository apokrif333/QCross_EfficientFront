import hashlib
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import IngestionState, Instrument
from app.ingestion.base import CompletenessBaseline, IngestionError, InstrumentRecord, SyncStats


def list_instruments(
    session: Session,
    *,
    source: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    search: str | None = None,
    currency: str | None = None,
    available_currency: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[Instrument]:
    query = select(Instrument)
    if source is not None:
        query = query.where(Instrument.source == source)
    if category is not None:
        query = query.where(Instrument.category == category)
    if active is not None:
        query = query.where(Instrument.is_active == active)
    if currency is not None:
        query = query.where(Instrument.currency == currency.upper())
    if available_currency is not None:
        code = available_currency.upper()
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            currencies = func.json_each(Instrument.available_currencies).table_valued("value")
            query = query.where(
                exists(select(1).select_from(currencies).where(currencies.c.value == code))
            )
        elif dialect == "postgresql":
            query = query.where(cast(Instrument.available_currencies, JSONB).contains([code]))
        else:
            raise IngestionError(f"Unsupported currency filtering database: {dialect}")
    if search:
        query = query.where(
            or_(
                Instrument.source_symbol.icontains(search, autoescape=True),
                Instrument.name.icontains(search, autoescape=True),
            )
        )
    return list(
        session.scalars(
            query.order_by(Instrument.source, Instrument.source_symbol).limit(limit).offset(offset)
        )
    )


class InstrumentRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def synchronize(
        self,
        source: str,
        records: list[InstrumentRecord],
        observed_at: datetime,
        validate: Callable[[CompletenessBaseline], None],
    ) -> SyncStats:
        """Serialize writers, validate against stored history, then atomically upsert.

        The source service must provide a completeness validator. It runs under the
        writer lock *before* any records can change. Any exception rolls back all work.
        Stats distinguish metadata changes from unchanged records refreshed as seen.
        """
        if not records or len({r.source_symbol for r in records}) != len(records):
            raise IngestionError("Cannot synchronize an empty universe or duplicate logical keys")
        if observed_at.tzinfo is None:
            raise IngestionError("Discovery timestamp must be timezone-aware")
        stats = SyncStats()
        with self.session_factory() as session, session.begin():
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                # Serialize the complete read/validate/write transaction, including first import.
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            elif dialect == "postgresql":
                lock_key = int.from_bytes(hashlib.sha256(source.encode()).digest()[:8], signed=True)
                session.execute(select(func.pg_advisory_xact_lock(lock_key)))
            else:
                raise IngestionError(f"Unsupported synchronization database: {dialect}")
            state = session.get(IngestionState, source)
            existing = {
                i.source_symbol: i
                for i in session.scalars(select(Instrument).where(Instrument.source == source))
            }
            if state is None:
                state = IngestionState(
                    source=source, last_count=0, high_water_count=0, category_counts={}
                )
                session.add(state)
            active_rows = [i for i in existing.values() if i.is_active]
            last_success = state.last_success_at
            if last_success is not None and last_success.tzinfo is None:
                last_success = last_success.replace(tzinfo=UTC)
            if last_success is not None and observed_at < last_success:
                raise IngestionError(
                    "An older discovery cannot replace a newer successful universe"
                )
            baseline = CompletenessBaseline(
                high_water_count=max(state.high_water_count, len(active_rows)),
                category_counts=state.category_counts
                or dict(Counter(i.category or "Unclassified" for i in active_rows)),
                last_success_at=last_success,
            )
            validate(baseline)

            seen: set[str] = set()
            for record in records:
                seen.add(record.source_symbol)
                values = record.model_dump()
                row = existing.get(record.source_symbol)
                if row is None:
                    session.add(
                        Instrument(
                            source=source,
                            **values,
                            is_active=True,
                            first_seen_at=observed_at,
                            last_seen_at=observed_at,
                            created_at=observed_at,
                            updated_at=observed_at,
                        )
                    )
                    stats.inserted += 1
                else:
                    changed = not row.is_active or any(
                        getattr(row, key) != value for key, value in values.items()
                    )
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.is_active = True
                    row.last_seen_at = observed_at
                    row.updated_at = observed_at
                    if changed:
                        stats.updated += 1
                    else:
                        stats.unchanged += 1
            for symbol, row in existing.items():
                if symbol not in seen and row.is_active:
                    row.is_active = False
                    row.updated_at = observed_at
                    stats.deactivated += 1
            state.last_count = len(records)
            state.high_water_count = max(baseline.high_water_count, len(records))
            state.category_counts = dict(Counter(r.category or "Unclassified" for r in records))
            state.last_success_at = observed_at
        return stats
