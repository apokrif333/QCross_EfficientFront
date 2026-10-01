from collections import Counter
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LazyPortfolioInstrument(BaseModel):
    source_symbol: str = Field(min_length=1)
    name: str | None = None
    category: str | None = None
    currency: str | None = None
    available_currencies: list[str] | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "category")
    @classmethod
    def normalize_whitespace(cls, value: str | None) -> str | None:
        return " ".join(value.split()) or None if value is not None else None


class DuplicateConflict(BaseModel):
    source_symbol: str
    variants: list[LazyPortfolioInstrument]


class ParseDiagnostics(BaseModel):
    mechanism: str = "html_select_options"
    selector_count: int = 0
    selector_option_counts: list[int] = Field(default_factory=list)
    raw_instrument_records: int = 0
    duplicate_records: int = 0
    placeholder_options: int = 0
    missing_symbols: int = 0
    empty_names: list[str] = Field(default_factory=list)
    unclassified_symbols: list[str] = Field(default_factory=list)
    missing_currencies: list[str] = Field(default_factory=list)
    missing_currency_availability: list[str] = Field(default_factory=list)
    conflicts: list[DuplicateConflict] = Field(default_factory=list)
    panel_symbol_count: int = 0
    panel_missing_from_selector: list[str] = Field(default_factory=list)
    selector_only_symbols: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ParseResult(BaseModel):
    instruments: list[LazyPortfolioInstrument] = Field(default_factory=list)
    diagnostics: ParseDiagnostics = Field(default_factory=ParseDiagnostics)

    @property
    def category_counts(self) -> dict[str, int]:
        return dict(Counter(i.category or "Unclassified" for i in self.instruments))

    @property
    def currency_counts(self) -> dict[str, int]:
        return dict(Counter(i.currency or "Unknown" for i in self.instruments))

    @property
    def available_currency_counts(self) -> dict[str, int]:
        return dict(
            Counter(code for i in self.instruments for code in i.available_currencies or [])
        )


class ValidationReport(BaseModel):
    passed: bool
    instrument_count: int
    unique_symbol_count: int
    category_counts: dict[str, int]
    baseline_count: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
