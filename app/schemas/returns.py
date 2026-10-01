from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReturnSeriesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    source: str
    currency: str
    frequency: str
    return_type: str
    extraction_method: str
    currency_kind: str
    validation_status: str
    is_extended_history: bool | None
    start_date: date
    end_date: date
    observation_count: int
    last_updated_at: datetime
    metadata: dict[str, Any] = Field(validation_alias="source_metadata")

    @field_validator("last_updated_at")
    @classmethod
    def restore_sqlite_timezone(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class MonthlyReturnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    series_id: int
    date: date
    return_value: Decimal
    quality_flag: str
    source_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def restore_sqlite_timezone(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
