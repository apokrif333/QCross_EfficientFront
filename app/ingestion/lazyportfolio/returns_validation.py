"""Precision-aware consistency checks against the site's separate capital/KPI arrays."""

import logging
import math
import statistics
from datetime import UTC, date, datetime
from decimal import Decimal

from app.ingestion.lazyportfolio.returns_models import ExtractedReturns, ReturnsValidation
from app.ingestion.lazyportfolio.returns_parser import month_end, shift_month

logger = logging.getLogger(__name__)
ONE = Decimal(1)
CAPITAL_HALF_QUANTUM = Decimal("0.00005")  # capitalBase is rounded to four decimals
KPI_HALF_QUANTUM = Decimal("0.0000005")  # four decimal places in percentage units


def validate_returns(data: ExtractedReturns, *, today: date | None = None) -> ReturnsValidation:
    report = ReturnsValidation(warnings=list(data.warnings))
    obs = data.observations
    dates = [o.date for o in obs]
    date_set = set(dates)
    values = [o.return_value for o in obs]
    count = (data.end_date.year - data.start_date.year) * 12
    count += data.end_date.month - data.start_date.month + 1
    if (
        data.source_symbol == "VTI"
        and data.currency == "USD"
        and data.start_date != date(1793, 1, 31)
    ):
        report.errors.append("VTI pilot must include the published history starting January 1793")
    if count < 1 or len(obs) != count or len(obs) != data.expected_count:
        report.errors.append("Observation count does not match declared complete calendar span")
    expected_dates = [shift_month(data.start_date, i) for i in range(max(0, count))]
    if dates != expected_dates:
        report.errors.append("Duplicate, missing, unordered or non-month-end observations")
    current = today or datetime.now(UTC).date()
    if data.end_date >= month_end(current.year, current.month):
        report.errors.append("Series contains the current incomplete or a future month")
    if not values or any(not r.is_finite() or r < -1 for r in values):
        report.errors.append("Empty series, non-finite values or simple return below -100%")
    if len(data.capital) != len(obs) + 1:
        report.errors.append("Capital-growth array length does not match monthly observations")
    if any(not c.is_finite() or c <= 0 for c in data.capital):
        report.errors.append("Invalid or non-positive source capital-growth observations")
    report.checks["structure"] = {
        "first_month": str(data.start_date),
        "last_month": str(data.end_date),
        "count": len(obs),
        "calendar_months": count,
        "duplicate_dates": len(dates) - len(set(dates)),
        "missing_months": [str(d) for d in expected_dates if d not in date_set],
        "precision_percent_decimals": data.percent_decimal_places,
    }
    outliers = [
        {"date": str(o.date), "return": str(o.return_value)}
        for o in obs
        if o.return_value.is_finite() and abs(o.return_value) > Decimal("0.5")
    ]
    report.checks["reviewable_outliers"] = outliers
    if outliers:
        report.warnings.append(
            f"{len(outliers)} returns exceed 50% in magnitude; retained for review"
        )
    if report.errors:
        return report
    epsilon = Decimal(10) ** (-data.percent_decimal_places) / 200
    growth = [ONE]
    lower = [ONE]
    upper = [ONE]
    peak = ONE
    drawdown = Decimal(0)
    growth_failures = []
    # Bounds compound the half-unit rounding uncertainty, without changing observations.
    for i, r in enumerate(values, 1):
        growth.append(growth[-1] * (ONE + r))
        lower.append(lower[-1] * max(Decimal(0), ONE + r - epsilon))
        upper.append(upper[-1] * (ONE + r + epsilon))
        peak = max(peak, growth[-1])
        drawdown = min(drawdown, growth[-1] / peak - ONE)
        # Account for rounding of both source baseline and source capital point.
        source_low = (data.capital[i] - CAPITAL_HALF_QUANTUM) / (
            data.capital[0] + CAPITAL_HALF_QUANTUM
        )
        source_high = (data.capital[i] + CAPITAL_HALF_QUANTUM) / (
            data.capital[0] - CAPITAL_HALF_QUANTUM
        )
        if upper[-1] < source_low or lower[-1] > source_high:
            growth_failures.append(str(obs[i - 1].date))
    if growth_failures:
        report.errors.append(f"Capital-growth path inconsistent at {len(growth_failures)} months")
    power = 12 / len(obs)
    annualized = Decimal(str(float(growth[-1]) ** power - 1))
    volatility = Decimal(str(statistics.pstdev(map(float, values)) * math.sqrt(12)))
    annualized_low = Decimal(str(float(lower[-1]) ** power - 1))
    annualized_high = Decimal(str(float(upper[-1]) ** power - 1))
    # Any perturbation bounded by epsilon changes population stdev by at most epsilon.
    vol_tolerance = epsilon * Decimal(str(math.sqrt(12))) + KPI_HALF_QUANTUM
    # Bound maximum drawdown from the cumulative rounding envelopes at every point.
    low_peak = high_peak = ONE
    dd_lower = dd_upper = Decimal(0)
    for low, high in zip(lower, upper, strict=True):
        low_peak = max(low_peak, low)
        high_peak = max(high_peak, high)
        dd_lower = min(dd_lower, low / high_peak - ONE)
        dd_upper = min(dd_upper, min(Decimal(0), high / low_peak - ONE))
    calculated = {
        "final_capital": growth[-1],
        "annualized_return": annualized,
        "annualized_volatility": volatility,
        "maximum_drawdown": drawdown,
    }
    limits = {
        "final_capital": (lower[-1] - CAPITAL_HALF_QUANTUM, upper[-1] + CAPITAL_HALF_QUANTUM),
        "annualized_return": (
            annualized_low - KPI_HALF_QUANTUM,
            annualized_high + KPI_HALF_QUANTUM,
        ),
        "annualized_volatility": (volatility - vol_tolerance, volatility + vol_tolerance),
        "maximum_drawdown": (dd_lower - KPI_HALF_QUANTUM, dd_upper + KPI_HALF_QUANTUM),
    }
    stats = {}
    for key, value in calculated.items():
        published = data.published_statistics[key]
        low, high = limits[key]
        passed = published.is_finite() and low <= published <= high
        stats[key] = {
            "calculated": str(value),
            "published": str(published),
            "difference": str(value - published),
            "allowed_min": str(low),
            "allowed_max": str(high),
            "passed": passed,
        }
        if not passed:
            report.errors.append(f"Published {key} does not match extracted returns")
    if values[-1] != data.published_latest_return:
        report.errors.append("Latest completed monthly return differs from published 1M slice")
    annual = []
    for year in range(data.start_date.year, data.end_date.year + 1):
        indices = [i for i, o in enumerate(obs) if o.date.year == year]
        first, last = indices[0], indices[-1] + 1
        product = low = high = ONE
        for r in values[first:last]:
            product *= ONE + r
            low *= max(Decimal(0), ONE + r - epsilon)
            high *= ONE + r + epsilon
        cap_first, cap_last = data.capital[first], data.capital[last]
        pub = cap_last / cap_first - ONE
        pub_low = (cap_last - CAPITAL_HALF_QUANTUM) / (cap_first + CAPITAL_HALF_QUANTUM)
        pub_high = (cap_last + CAPITAL_HALF_QUANTUM) / (cap_first - CAPITAL_HALF_QUANTUM)
        passed = not (high < pub_low or low > pub_high)
        annual.append(
            {
                "year": year,
                "months": last - first,
                "return": str(product - ONE),
                "published_from_capital": str(pub),
                "passed": passed,
            }
        )
        if not passed:
            report.errors.append(f"Historical annual return inconsistent for {year}")
    report.checks.update(
        {
            "statistics": stats,
            "annual_returns": annual,
            "capital_path": {
                "points_checked": len(obs),
                "failed_months": growth_failures,
                "final_relative_error": str(growth[-1] / data.capital[-1] - ONE),
            },
            "latest_months": [
                {"date": str(o.date), "return": str(o.return_value)} for o in obs[-12:]
            ],
            "proxy_cutoff": str(data.proxy_cutoff) if data.proxy_cutoff else None,
            "comparison_scope": (
                "Internal source consistency; not independent market-data verification"
            ),
        }
    )
    report.passed = not report.errors
    logger.info(
        "lazyportfolio.returns.validation.completed symbol=%s passed=%s errors=%d",
        data.source_symbol,
        report.passed,
        len(report.errors),
    )
    return report
