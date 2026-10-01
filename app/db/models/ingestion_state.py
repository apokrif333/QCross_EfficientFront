from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionState(Base):
    """Durable completeness baseline; updated in the same transaction as the universe."""

    __tablename__ = "ingestion_states"

    source: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_count: Mapped[int] = mapped_column(Integer, default=0)
    high_water_count: Mapped[int] = mapped_column(Integer, default=0)
    category_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
