"""Versioned macro references; BIL points at existing nominal observations."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow
from app.db.models.returns import ExactReturn


class ReferenceSeries(Base):
    __tablename__ = "reference_series"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_code: Mapped[str] = mapped_column(String(80), unique=True)
    source: Mapped[str] = mapped_column(String(80), default="lazyportfolioetf")
    frequency: Mapped[str] = mapped_column(String(20), default="monthly")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    units: Mapped[str] = mapped_column(String(40), default="decimal_fraction")
    return_series_id: Mapped[int | None] = mapped_column(ForeignKey("return_series.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    observation_count: Mapped[int] = mapped_column(Integer)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    data_version: Mapped[str] = mapped_column(String(64))
    source_metadata: Mapped[dict] = mapped_column(JSON)


class ReferenceObservation(Base):
    __tablename__ = "reference_observations"
    __table_args__ = (UniqueConstraint("series_id", "month"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("reference_series.id"), index=True)
    month: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(ExactReturn())
    status: Mapped[str] = mapped_column(String(40))
    source_snapshot: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReferenceRevision(Base):
    __tablename__ = "reference_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("reference_series.id"))
    month: Mapped[date] = mapped_column(Date)
    old_value: Mapped[Decimal] = mapped_column(ExactReturn())
    new_value: Mapped[Decimal] = mapped_column(ExactReturn())
    old_status: Mapped[str] = mapped_column(String(40))
    new_status: Mapped[str] = mapped_column(String(40))
    source_snapshot: Mapped[str] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
