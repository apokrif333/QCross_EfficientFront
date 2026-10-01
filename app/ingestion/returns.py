"""Provider-independent contract between extraction services and storage."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ReturnObservation(BaseModel):
    date: date
    return_value: Decimal
    quality_flag: str
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class ValidatedReturnData(Protocol):
    source: str
    source_symbol: str
    currency: str
    source_url: str
    extracted_at: datetime
    start_date: date
    end_date: date
    observations: list[ReturnObservation]

    def require_valid(self) -> None: ...

    @property
    def storage_metadata(self) -> dict[str, Any]: ...

    @property
    def is_extended_history(self) -> bool | None: ...
