import logging
import math

from app.config import Settings
from app.ingestion.base import CompletenessBaseline, IngestionError
from app.ingestion.lazyportfolio.models import ParseResult, ValidationReport

logger = logging.getLogger(__name__)
SENTINEL_SYMBOLS = frozenset({"VTI", "SPY", "TLT", "GLD"})


class DiscoveryValidationError(IngestionError):
    def __init__(self, result: ParseResult, report: ValidationReport) -> None:
        self.result = result
        self.report = report
        super().__init__("Discovery validation failed: " + "; ".join(report.errors))


def validate_discovery(
    result: ParseResult,
    settings: Settings,
    baseline: CompletenessBaseline | None = None,
) -> ValidationReport:
    baseline = baseline or CompletenessBaseline()
    errors = list(result.diagnostics.errors)
    symbols = {item.source_symbol for item in result.instruments}
    count = len(result.instruments)
    if count < settings.lazyportfolio_min_instruments:
        errors.append(
            f"Implausibly low instrument count: {count}; "
            f"minimum {settings.lazyportfolio_min_instruments}"
        )
    if len(symbols) != count:
        errors.append("Duplicate symbols remain in normalized discovery")
    missing = sorted(SENTINEL_SYMBOLS - symbols)
    if missing:
        errors.append(f"Missing sanity-check symbols: {missing}")
    if result.diagnostics.missing_symbols:
        errors.append(f"Missing option identifiers: {result.diagnostics.missing_symbols}")
    if result.diagnostics.conflicts and not any("Conflicting" in e for e in errors):
        errors.append("Conflicting duplicate symbols")
    minimum_fraction = 1 - settings.lazyportfolio_max_drop_fraction
    minimum_count = math.ceil(baseline.high_water_count * minimum_fraction)
    if count < minimum_count:
        errors.append(
            f"Universe dropped from high-water {baseline.high_water_count} to {count}; "
            f"minimum allowed {minimum_count}"
        )
    categories = result.category_counts
    for category, previous_count in baseline.category_counts.items():
        current_count = categories.get(category, 0)
        minimum = math.ceil(previous_count * minimum_fraction)
        if current_count < minimum:
            errors.append(
                f"Category {category!r} dropped from {previous_count} "
                f"to {current_count}; minimum allowed {minimum}"
            )
    report = ValidationReport(
        passed=not errors,
        instrument_count=count,
        unique_symbol_count=len(symbols),
        category_counts=categories,
        baseline_count=baseline.high_water_count,
        errors=errors,
        warnings=result.diagnostics.warnings,
    )
    logger.info(
        "lazyportfolio.validation.completed passed=%s instruments=%d errors=%d",
        report.passed,
        count,
        len(errors),
    )
    if errors:
        raise DiscoveryValidationError(result, report)
    return report
