"""Inflation from the original BIL capital arrays, using exact Decimal ratios."""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext

from app.ingestion.lazyportfolio.returns_models import ReturnExtractionError
from app.ingestion.lazyportfolio.returns_parser import embedded_state, parse_returns, shift_month

# Maintained publication exceptions, NOT a numerical zero detector. Remove/resolve
# explicitly when source publication is confirmed; never infer status from magnitude.
UNPUBLISHED = {
    "2025-10": "Missing published observation in the investigated source dataset",
    "2026-10": "Unpublished in the verified October 2026 source snapshot",
}


@dataclass(frozen=True)
class InflationObservation:
    month: date
    value: Decimal
    status: str


def extract_inflation(
    html: str, *, source_url: str, extracted_at: datetime, published_months: tuple[str, ...] = ()
) -> tuple[list, object, str]:
    nominal = parse_returns(
        html, symbol="BIL", currency="USD", source_url=source_url, extracted_at=extracted_at
    )
    state = embedded_state(html)
    portfolio = next(
        p
        for p in state["portfolios"].values()
        if p.get("pID") == "BIL" and p.get("components", {}).get("weights") == {"BIL": 100}
    )
    section = portfolio["rend"]["sliceArray"]["MAX"]
    try:
        arrays = [section["capitalBase"], section["capitalNoInfl"]]
        if any(not isinstance(a, list) or len(a) != len(nominal.observations) + 1 for a in arrays):
            raise ReturnExtractionError("Capital arrays must both have len(rendList)+1 values")
        capitals = []
        for array in arrays:
            if any(isinstance(x, (bool, str)) or x is None for x in array):
                raise ReturnExtractionError("Capital arrays require JSON numbers")
            values = [Decimal(x) for x in array]
            if any(not x.is_finite() or x <= 0 for x in values):
                raise ReturnExtractionError("Capital observations must be finite and positive")
            capitals.append(values)
        if (
            nominal.observations[-1].date != nominal.end_date
            or (shift_month(nominal.start_date, len(nominal.observations) - 1) != nominal.end_date)
            or nominal.expected_count != len(nominal.observations)
        ):
            raise ReturnExtractionError("Monthly range/count mismatch")
        rows = []
        n, r = capitals
        with localcontext() as context:
            context.prec = 60
            for i, observation in enumerate(nominal.observations, 1):
                month = str(observation.date)[:7]
                provisional = month in UNPUBLISHED and month not in published_months
                value = Decimal(0) if provisional else n[i] * r[i - 1] / (r[i] * n[i - 1]) - 1
                status = (
                    "assumed_zero_unpublished"
                    if provisional
                    else (
                        "historical_reconstruction" if observation.date.year < 1913 else "published"
                    )
                )
                rows.append(
                    InflationObservation(observation.date, value.quantize(Decimal("1e-18")), status)
                )
        return rows, nominal, hashlib.sha256(html.encode()).hexdigest()
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ReturnExtractionError(f"Invalid BIL inflation arrays: {exc}") from exc
