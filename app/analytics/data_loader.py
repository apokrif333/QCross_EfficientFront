"""Read-only SQLAlchemy boundary; all mathematical modules receive plain arrays."""

import hashlib
import json
from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.models import AnalyticsError, ReturnPanel
from app.db.models import Instrument, MonthlyReturn, ReturnSeries

# Explicit source category mappings. No ticker-based classification.
CATEGORY_GROUPS = {
    "Global / ex-US Stocks": ["Equity"],
    "US Factor - Dividends - Misc": ["Equity"],
    "US Theme - Sectors": ["Equity"],
    "CA Fixed Income": ["Fixed Income"],
    "EU Fixed Income": ["Fixed Income"],
    "International Fixed Income": ["Fixed Income"],
    "UK Fixed Income": ["Fixed Income"],
    "US Fixed Income": ["Fixed Income"],
    "Commodity": ["Commodities"],
    "US Stocks": ["Equity"],
    "International Stocks": ["Equity"],
    "Global Stocks": ["Equity"],
    "Emerging Markets Stocks": ["Equity"],
    "US Bonds": ["Fixed Income"],
    "International Bonds": ["Fixed Income"],
    "Global Bonds": ["Fixed Income"],
    "Commodities": ["Commodities"],
}


def instrument_groups(instrument: Instrument) -> list[str]:
    memberships = {g for g in (instrument.category, instrument.subcategory) if g}
    memberships.update(CATEGORY_GROUPS.get(instrument.category, []))
    explicit_groups = instrument.raw_metadata.get("analytics_groups", [])
    if isinstance(explicit_groups, list):
        memberships.update(g for g in explicit_groups if isinstance(g, str))
    return sorted(memberships)


def align_returns(
    series: dict[str, pd.Series], *, start_date: date | None = None, end_date: date | None = None
) -> pd.DataFrame:
    if not 2 <= len(series) <= 15:
        raise AnalyticsError("Frontier analysis requires 2 to 15 assets.")
    normalized = {}
    for asset, observations in series.items():
        if observations.empty:
            raise AnalyticsError(f"No validated USD monthly total-return observations for {asset}.")
        observations = observations.copy()
        observations.index = pd.PeriodIndex(observations.index, freq="M")
        if observations.index.has_duplicates:
            raise AnalyticsError(f"Duplicate monthly observations for {asset}.")
        normalized[asset] = observations.sort_index()
    start = max(s.index.min() for s in normalized.values())
    end = min(s.index.max() for s in normalized.values())
    if start_date:
        start = max(start, pd.Period(start_date, freq="M"))
    if end_date:
        end = min(end, pd.Period(end_date, freq="M"))
    if start > end:
        raise AnalyticsError("The selected assets and requested dates have no common period.")
    months = pd.period_range(start, end, freq="M")
    result = pd.DataFrame({a: s.reindex(months) for a, s in normalized.items()})
    for asset in result:
        missing = result.index[result[asset].isna()]
        if len(missing):
            raise AnalyticsError(
                f"The aligned period contains missing months for {asset}: "
                + ", ".join(str(m) for m in missing[:12])
            )
    ReturnPanel(result, list(result.columns))
    return result


def load_returns(
    session: Session,
    instrument_ids: list[int],
    *,
    currency: str = "USD",
    start_date: date | None = None,
    end_date: date | None = None,
    today: date | None = None,
) -> ReturnPanel:
    if currency != "USD":
        raise AnalyticsError(
            "Only USD calculations are supported; no currency conversion is applied."
        )
    if len(instrument_ids) > 15:
        raise AnalyticsError("A maximum of 15 assets is supported. Please reduce your selection.")
    if len(instrument_ids) < 2 or len(set(instrument_ids)) != len(instrument_ids):
        raise AnalyticsError("Select at least two distinct instrument IDs.")
    if start_date and end_date and start_date > end_date:
        raise AnalyticsError("start_date must not exceed end_date.")
    series, metadata, groups, warnings = {}, [], {}, []
    endpoints = []
    shared: dict[str, list[str]] = {}
    for instrument_id in instrument_ids:
        instrument = session.get(Instrument, instrument_id)
        if instrument is None:
            raise AnalyticsError(f"Instrument {instrument_id} does not exist.")
        candidates = list(
            session.scalars(
                select(ReturnSeries).where(
                    ReturnSeries.instrument_id == instrument_id,
                    ReturnSeries.currency == "USD",
                    ReturnSeries.validation_status == "validated",
                    ReturnSeries.frequency == "monthly",
                    ReturnSeries.return_type == "nominal_total_return",
                )
            )
        )
        if len(candidates) != 1:
            raise AnalyticsError(
                f"Instrument {instrument_id} requires exactly one validated USD monthly "
                f"total-return series; found {len(candidates)}."
            )
        row = candidates[0]
        observations = list(
            session.scalars(
                select(MonthlyReturn)
                .where(MonthlyReturn.series_id == row.id)
                .order_by(MonthlyReturn.date)
            )
        )
        if (
            not observations
            or len(observations) != row.observation_count
            or observations[0].date != row.start_date
            or observations[-1].date != row.end_date
        ):
            raise AnalyticsError(f"Series {row.id} observation metadata is inconsistent.")
        key = str(instrument_id)
        series[key] = pd.Series(
            [float(o.return_value) for o in observations],
            index=[o.date for o in observations],
            dtype=float,
        )
        memberships = instrument_groups(instrument)
        for group in memberships:
            groups.setdefault(group, []).append(key)
        source_meta = row.source_metadata
        content = {
            "series_id": row.id,
            "instrument_id": instrument_id,
            "source": row.source,
            "currency": row.currency,
            "frequency": row.frequency,
            "return_type": row.return_type,
            "observations": [[str(o.date), str(o.return_value)] for o in observations],
        }
        series_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        updated = row.last_updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        metadata.append(
            {
                "instrument_id": instrument_id,
                "ticker": instrument.ticker,
                "series_id": row.id,
                "series_version": series_hash,
                "series_sha256": series_hash,
                "last_updated_at": updated.isoformat(),
                "observation_count": row.observation_count,
                "currency": row.currency,
                "source": row.source,
                "start_date": str(row.start_date),
                "end_date": str(row.end_date),
                "currency_kind": row.currency_kind,
                "groups": sorted(memberships),
                "historical_proxy_through": source_meta.get("historical_proxy_through"),
                "detailed_proxy_transitions": source_meta.get(
                    "detailed_proxy_transitions", "unknown"
                ),
            }
        )
        if row.is_extended_history:
            warnings.append(
                f"{instrument.ticker} contains historical reconstructions; detailed proxy "
                "transitions may be unknown."
            )
        # Only affirmative metadata evidence establishes shared proxies.
        proxy = source_meta.get("shared_historical_proxy_id")
        if proxy:
            shared.setdefault(str(proxy), []).append(instrument.ticker)
        component = source_meta.get("source_component_symbol")
        if component and source_meta.get("historical_proxy_through"):
            shared.setdefault(f"component:{row.source}:{component}", []).append(instrument.ticker)
        endpoints.append(row.end_date)
    for proxy, tickers in shared.items():
        if len(tickers) > 1:
            warnings.append(
                f"Metadata establishes shared historical reconstruction {proxy}: {tickers}."
            )
    if len(set(endpoints)) > 1:
        warnings.append("Selected series have unequal publication endpoints; the earliest is used.")
    today = today or datetime.now(UTC).date()
    if pd.Period(today, freq="M").ordinal - pd.Period(min(endpoints), freq="M").ordinal > 2:
        warnings.append("One or more selected series have stale publication endpoints (>2 months).")
    frame = align_returns(series, start_date=start_date, end_date=end_date)
    if len(frame) < 180:
        warnings.append("Short common history: fewer than 180 monthly observations.")
    return ReturnPanel(frame, list(frame.columns), groups, metadata, warnings)
