from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class InstrumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_symbol: str
    ticker: str
    name: str | None
    category: str | None
    subcategory: str | None
    currency: str | None
    available_currencies: list[str] | None
    country: str | None
    exchange: str | None
    asset_type: str | None
    source_url: str | None
    is_active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    raw_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_validator("first_seen_at", "last_seen_at", "created_at", "updated_at")
    @classmethod
    def restore_sqlite_timezone(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
