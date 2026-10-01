from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.db.base import Base, utcnow


class ExactReturn(TypeDecorator[Decimal]):
    """Native decimal in PostgreSQL; decimal text in SQLite (no float round trip)."""

    impl = Numeric(38, 18)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine:
        return dialect.type_descriptor(String(64) if dialect.name == "sqlite" else self.impl)

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Decimal | str | None:
        if value is None:
            return None
        return str(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(value)


class ReturnSeries(Base):
    __tablename__ = "return_series"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "source",
            "currency",
            "frequency",
            "return_type",
            name="uq_return_series_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    source: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3))
    frequency: Mapped[str] = mapped_column(String(20), default="monthly")
    return_type: Mapped[str] = mapped_column(String(50), default="nominal_total_return")
    extraction_method: Mapped[str] = mapped_column(String(30), default="individual_page")
    currency_kind: Mapped[str] = mapped_column(String(20), default="native")
    validation_status: Mapped[str] = mapped_column(String(20), default="validated")
    is_extended_history: Mapped[bool | None] = mapped_column(Boolean)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    observation_count: Mapped[int] = mapped_column(Integer)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON)


class MonthlyReturn(Base):
    __tablename__ = "monthly_returns"
    __table_args__ = (UniqueConstraint("series_id", "date", name="uq_monthly_returns_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("return_series.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    return_value: Mapped[Decimal] = mapped_column(ExactReturn())
    quality_flag: Mapped[str] = mapped_column(String(100))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReturnRevision(Base):
    __tablename__ = "return_revisions"
    __table_args__ = (
        UniqueConstraint(
            "series_id",
            "date",
            "old_value",
            "new_value",
            name="uq_return_revision_values",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("return_series.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    old_value: Mapped[Decimal] = mapped_column(ExactReturn())
    new_value: Mapped[Decimal] = mapped_column(ExactReturn())
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReturnImport(Base):
    """Persistent per-instrument checkpoint; successes are skipped on batch resume."""

    __tablename__ = "return_imports"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "source",
            "currency",
            name="uq_return_import_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    source: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(40))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
