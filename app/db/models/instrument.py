from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("source", "source_symbol", name="uq_instruments_source_symbol"),
        Index("ix_instruments_source_active", "source", "is_active"),
        Index("ix_instruments_category", "category"),
        Index("ix_instruments_currency", "currency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(100))
    source_symbol: Mapped[str] = mapped_column(String(255))
    ticker: Mapped[str] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(255))
    subcategory: Mapped[str | None] = mapped_column(String(255))
    currency: Mapped[str | None] = mapped_column(String(32))
    available_currencies: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    country: Mapped[str | None] = mapped_column(String(100))
    exchange: Mapped[str | None] = mapped_column(String(100))
    asset_type: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
