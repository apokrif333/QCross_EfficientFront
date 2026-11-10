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


def attach_references(
    session,
    panel,
    *,
    return_basis="nominal",
    risk_free_source="historical_bil",
    risk_free_rate=0.03,
):
    """One database pass per request, before jobs and all numerical loops."""
    import numpy as np
    from sqlalchemy import inspect

    from app.analytics.references import real_returns
    from app.db.models.reference import ReferenceObservation, ReferenceSeries
    from app.ingestion.lazyportfolio.inflation import UNPUBLISHED
    from app.ingestion.lazyportfolio.macro_service import INFLATION, bil_series

    months = panel.returns.index
    refs = {
        "effective_start_date": str(months[0]),
        "effective_end_date": str(months[-1]),
        "effective_observation_count": len(months),
    }
    if risk_free_source == "historical_bil":
        try:
            bil = bil_series(session)
        except ValueError as exc:
            raise AnalyticsError(str(exc)) from exc
        rows = list(session.scalars(select(MonthlyReturn).where(MonthlyReturn.series_id == bil.id)))
        by_month = {pd.Period(r.date, freq="M"): r.return_value for r in rows}
        missing = [str(m) for m in months if m not in by_month]
        if missing:
            raise AnalyticsError(
                f"BIL reference lacks requested months {missing[:12]}; "
                "refresh validated BIL returns."
            )
        risk_free = np.array([float(by_month[m]) for m in months])
        version = hashlib.sha256(
            json.dumps([[str(m), str(by_month[m])] for m in months]).encode()
        ).hexdigest()
        refs.update(
            risk_free_instrument="BIL",
            risk_free_source_series_id=bil.id,
            risk_free_data_version=version,
            bil_start_date=str(bil.start_date),
            bil_end_date=str(bil.end_date),
            bil_observation_count=bil.observation_count,
        )
    else:
        if risk_free_rate <= -1:
            raise AnalyticsError("Fixed nominal annual risk-free rate must exceed -100%.")
        risk_free = np.full(len(months), np.expm1(np.log1p(risk_free_rate) / 12))
        refs.update(fixed_annual_rate=risk_free_rate, monthly_convention="(1+annual)^(1/12)-1")
    if return_basis == "real":
        if not inspect(session.get_bind()).has_table("reference_series"):
            raise AnalyticsError(
                "Inflation reference unavailable: run Alembic upgrade head "
                "and macro import-bil-inflation."
            )
        series = session.scalar(
            select(ReferenceSeries).where(ReferenceSeries.series_code == INFLATION)
        )
        if series is None:
            raise AnalyticsError("Inflation reference unavailable: run macro import-bil-inflation.")
        rows = {
            pd.Period(o.month, freq="M"): o
            for o in session.scalars(
                select(ReferenceObservation).where(ReferenceObservation.series_id == series.id)
            )
        }
        rates, statuses = [], []
        for month in months:
            row = rows.get(month)
            if row is None and str(month) not in UNPUBLISHED:
                raise AnalyticsError(
                    f"Unknown inflation gap at {month}; import/resolve source history, "
                    "no automatic zero fill."
                )
            rates.append(float(row.value) if row else 0.0)
            statuses.append(row.status if row else "assumed_zero_unpublished")
        inflation = np.array(rates)
        refs.update(
            inflation_source="LazyPortfolioETF BIL capitalBase/capitalNoInfl",
            inflation_data_version=series.data_version,
            inflation_historical_reconstruction=[
                str(m)
                for m, s in zip(months, statuses, strict=True)
                if s == "historical_reconstruction"
            ],
            inflation_assumed_zero_months=[
                str(m)
                for m, s in zip(months, statuses, strict=True)
                if s == "assumed_zero_unpublished"
            ],
            inflation_observations=[
                [str(m), float(v), s] for m, v, s in zip(months, inflation, statuses, strict=True)
            ],
        )
        refs["inflation_assumed_zero_count"] = len(refs["inflation_assumed_zero_months"])
        panel.returns = pd.DataFrame(
            real_returns(panel.values, inflation), index=months, columns=panel.assets
        )
        risk_free = real_returns(risk_free, inflation)
    panel.return_basis, panel.risk_free_source = return_basis, risk_free_source
    panel.reference_metadata = refs
    # Legacy nominal fixed-mode keeps the existing model-based 3% objective.
    panel.risk_free_returns = (
        risk_free if risk_free_source == "historical_bil" or return_basis == "real" else None
    )
    return panel
