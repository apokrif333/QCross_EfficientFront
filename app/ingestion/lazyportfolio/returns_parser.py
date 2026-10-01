"""Read original nominal observations from the Alpine detail-page JSON, never execute JS."""

import calendar
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from app.ingestion.lazyportfolio.returns_models import (
    ExtractedReturns,
    ReturnExtractionError,
    ReturnObservation,
)

logger = logging.getLogger(__name__)


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def shift_month(value: date, offset: int) -> date:
    serial = value.year * 12 + value.month - 1 + offset
    year, month = divmod(serial, 12)
    return month_end(year, month + 1)


def parse_period(value: str) -> date:
    year, month = map(int, value.split("-"))
    return month_end(year, month)


def embedded_state(html: str) -> dict[str, Any]:
    states = []
    for script in BeautifulSoup(html, "lxml").find_all("script"):
        contents = script.string or script.get_text()
        match = re.search(r"\bconst\s+_d\s*=\s*", contents)
        if match:
            try:
                state, _ = json.JSONDecoder(parse_float=Decimal).raw_decode(contents[match.end() :])
                states.append(state)
            except (ValueError, TypeError) as exc:
                raise ReturnExtractionError("Truncated or invalid detail-page JSON") from exc
    if len(states) != 1 or not isinstance(states[0], dict):
        raise ReturnExtractionError("Expected exactly one Alpine const _d detail-page JSON object")
    return states[0]


def parse_returns(
    html: str,
    *,
    symbol: str,
    currency: str,
    source_url: str,
    extracted_at: datetime,
) -> ExtractedReturns:
    return _parse_returns(
        html,
        symbol=symbol,
        currency=currency,
        source_url=source_url,
        extracted_at=extracted_at,
        backtester=False,
    )


def _parse_returns(
    html: str,
    *,
    symbol: str,
    currency: str,
    source_url: str,
    extracted_at: datetime,
    backtester: bool,
    component_symbol: str | None = None,
) -> ExtractedReturns:
    try:
        component_symbol = component_symbol or symbol
        if not backtester and component_symbol != symbol:
            raise ReturnExtractionError("Individual-page identity cannot be remapped")
        state = embedded_state(html)
        settings = state["settings"]
        matches = [
            p
            for p in state["portfolios"].values()
            if (p.get("components", {}).get("weights") == {component_symbol: 100})
            if backtester or p.get("pID") == symbol
        ]
        if len(matches) != 1:
            raise ReturnExtractionError(f"Expected one exact instrument {symbol!r} in source state")
        portfolio = matches[0]
        if settings["currency"] != currency:
            raise ReturnExtractionError(
                f"Source currency {settings['currency']} differs from requested native {currency}"
            )
        if (
            bool(settings["isUserSimulation"]) != backtester
            or settings["withCashflow"]
            or settings["withTaxPaid"]
            or settings["withRebalancing"]
            or Decimal(str(settings.get("contributionAmount", 0))) != 0
            or portfolio.get("filter")
            or portfolio.get("withTaxPaid")
            or portfolio.get("withRebalancing")
            or (not backtester and not portfolio.get("isEtf"))
            or (backtester and not portfolio.get("isUserSimulation"))
            or (backtester and Decimal(str(portfolio.get("capitaleIniziale", 0))) != 1)
            or portfolio.get("components", {}).get("weights") != {component_symbol: 100}
            or (component_symbol != symbol and not portfolio.get("etfSwap"))
            or any(portfolio.get("components", {}).get("taxation", {}).values())
        ):
            raise ReturnExtractionError(
                "Not an unfiltered, single-instrument nominal return series"
            )
        section = portfolio["rend"]["sliceArray"]["MAX"]
        if (
            section["periodoMin"] != portfolio["periodoMin"]
            or section["periodoMax"] != portfolio["periodoMax"]
        ):
            raise ReturnExtractionError(
                "MAX slice does not cover the instrument's declared full history"
            )
        start = parse_period(section["periodoMin"])
        end = parse_period(section["periodoMax"])
        baseline = parse_period(section["periodoStart"])
        if shift_month(baseline, 1) != start:
            raise ReturnExtractionError("Capital baseline is not the month before first return")
        values = section["rendList"]
        if not isinstance(values, list) or not values:
            raise ReturnExtractionError("Missing MAX.rendList original monthly observations")
        if any(isinstance(v, (bool, str)) or v is None for v in values):
            raise ReturnExtractionError("Monthly observations must be JSON numbers, not null/text")
        percentages = [Decimal(v) for v in values]
        if any(not p.is_finite() for p in percentages):
            raise ReturnExtractionError("Non-finite monthly observations")
        precision = max(max(0, -p.as_tuple().exponent) for p in percentages)
        if precision > 16:
            raise ReturnExtractionError("Source precision exceeds database decimal capacity")
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        reinvestment = re.search(
            r"dividend reinvestment\s*\(without dividend taxation\),?\s*when applicable",
            text,
            re.IGNORECASE,
        )
        if reinvestment is None:
            raise ReturnExtractionError(
                "Source does not confirm dividend-reinvested return conventions"
            )
        match = re.search(
            r"Returns,\s*up to\s+([A-Za-z]+\s+\d{4}),\s*have been derived[^.]+\.",
            text,
        )
        methodology = match.group(0) if match else None
        cutoff = None
        if match:
            parsed = datetime.strptime(match.group(1), "%B %Y")
            cutoff = month_end(parsed.year, parsed.month)
        observations = []
        for i, percent in enumerate(percentages):
            observed_month = shift_month(start, i)
            provenance = "unknown"
            if cutoff:
                provenance = "historical_proxy" if observed_month <= cutoff else "reported_etf_era"
            observations.append(
                ReturnObservation(
                    date=observed_month,
                    return_value=percent / Decimal(100),
                    quality_flag=f"rounded_{precision}dp_percent|{provenance}",
                    source_metadata={
                        "original_percent": str(percent),
                        "array_index": i,
                        "provenance": provenance,
                    },
                )
            )
        capital = [Decimal(v) for v in section["capitalBase"]]
        invested = section["capitaleInvestito"]
        if len(invested) != len(capital) or len(set(map(str, invested))) != 1:
            raise ReturnExtractionError("Capital-growth data includes contributions/withdrawals")
        kpi = portfolio["kpiData"]["MAX"]["base"]
        latest = portfolio["rend"]["sliceArray"]["1M"]
        if latest["periodoMax"] != section["periodoMax"] or len(latest["rendList"]) != 1:
            raise ReturnExtractionError("Latest-month slice is inconsistent with MAX")
        warnings = [
            f"Original JSON monthly percentages are rounded to at most {precision} decimals; "
            "not unrounded provider observations.",
            "Detailed proxy transitions are unknown; only the published broad cutoff is used."
            if cutoff
            else "No reconstruction cutoff published; observation provenance is unknown.",
        ]
        result = ExtractedReturns(
            source_symbol=symbol,
            currency=currency,
            source_url=source_url,
            extracted_at=extracted_at,
            start_date=start,
            end_date=end,
            expected_count=int(section["monthDiff"]),
            observations=observations,
            percent_decimal_places=precision,
            capital=capital,
            published_statistics={
                "final_capital": Decimal(kpi["finalCapital"]),
                "annualized_return": Decimal(kpi["returnAnn"]) / 100,
                "annualized_volatility": Decimal(kpi["stdDev"]) / 100,
                "maximum_drawdown": Decimal(kpi["maxDrawdown"]) / 100,
            },
            published_latest_return=Decimal(latest["rendList"][0]) / 100,
            proxy_cutoff=cutoff,
            methodology=methodology,
            dividend_reinvestment_statement=reinvestment.group(0),
            warnings=warnings,
            extraction_method="backtester" if backtester else "individual_page",
            source_component_symbol=component_symbol,
        )
        logger.info(
            "lazyportfolio.returns.parse.completed symbol=%s count=%d precision=%d",
            symbol,
            len(observations),
            precision,
        )
        return result
    except ReturnExtractionError:
        raise
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise ReturnExtractionError(f"Invalid monthly-return source structure: {exc}") from exc
