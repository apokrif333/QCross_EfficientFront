from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.ingestion.base import IngestionError
from app.ingestion.returns import ReturnObservation as ReturnObservation


class ReturnExtractionError(IngestionError):
    pass


class ExtractedReturns(BaseModel):
    source: str = "lazyportfolioetf"
    source_symbol: str
    currency: str
    source_url: str
    extracted_at: datetime
    start_date: date
    end_date: date
    expected_count: int
    observations: list[ReturnObservation]
    percent_decimal_places: int
    capital: list[Decimal]
    published_statistics: dict[str, Decimal]
    published_latest_return: Decimal
    proxy_cutoff: date | None = None
    methodology: str | None = None
    dividend_reinvestment_statement: str | None = None
    warnings: list[str] = Field(default_factory=list)
    snapshot_path: str | None = None
    snapshot_sha256: str | None = None
    snapshot_encoding: str = "utf-8"
    extraction_method: str = "individual_page"
    currency_kind: str = "native"
    conversion_methodology: str | None = None
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    source_component_symbol: str | None = None

    def require_valid(self) -> None:
        from app.ingestion.lazyportfolio.returns_validation import validate_returns

        report = validate_returns(self)
        if not report.passed:
            raise ReturnValidationError(report)

    @property
    def is_extended_history(self) -> bool | None:
        return self.start_date <= self.proxy_cutoff if self.proxy_cutoff else None

    @property
    def storage_metadata(self) -> dict[str, Any]:
        from app.ingestion.lazyportfolio.returns_validation import validate_returns

        return {
            "extraction_method": self.extraction_method,
            "currency_kind": self.currency_kind,
            "conversion_methodology": self.conversion_methodology,
            "request_metadata": self.request_metadata,
            "source_component_symbol": self.source_component_symbol or self.source_symbol,
            "extraction_mechanism": "Alpine const _d portfolios.*.rend.sliceArray.MAX.rendList",
            "source_url": self.source_url,
            "precision_percent_decimals": self.percent_decimal_places,
            "precision": "source_rounded",
            "methodology": self.methodology,
            "historical_proxy_through": str(self.proxy_cutoff) if self.proxy_cutoff else None,
            "detailed_proxy_transitions": "unknown",
            "dividends": "reinvested_as_reported",
            "dividend_reinvestment_statement": self.dividend_reinvestment_statement,
            "inflation_adjusted": False,
            "cashflows": False,
            "taxes": False,
            "snapshot_path": self.snapshot_path,
            "snapshot_sha256": self.snapshot_sha256,
            "snapshot_encoding": self.snapshot_encoding,
            "validation": validate_returns(self).model_dump(mode="json"),
        }


class ReturnsValidation(BaseModel):
    passed: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: dict[str, Any] = Field(default_factory=dict)


class ReturnValidationError(ReturnExtractionError):
    def __init__(self, report: ReturnsValidation) -> None:
        self.report = report
        super().__init__("Monthly returns failed validation: " + "; ".join(report.errors))
