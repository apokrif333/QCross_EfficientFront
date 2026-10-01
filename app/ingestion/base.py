from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class IngestionError(RuntimeError):
    """Expected source ingestion failure."""


class InstrumentRecord(BaseModel):
    """Source-neutral data handed to storage after source-specific validation."""

    source_symbol: str = Field(min_length=1, max_length=255)
    ticker: str = Field(min_length=1, max_length=255)
    name: str | None = None
    category: str | None = None
    subcategory: str | None = None
    currency: str | None = None
    available_currencies: list[str] | None = None
    country: str | None = None
    exchange: str | None = None
    asset_type: str | None = None
    source_url: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class CompletenessBaseline(BaseModel):
    high_water_count: int = 0
    category_counts: dict[str, int] = Field(default_factory=dict)
    last_success_at: datetime | None = None


class SyncStats(BaseModel):
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0


class UniverseRepository(Protocol):
    def synchronize(
        self,
        source: str,
        records: list[InstrumentRecord],
        observed_at: datetime,
        validate: Callable[[CompletenessBaseline], None],
    ) -> SyncStats: ...
