"""Transactional macro refresh; revisions are staged until explicitly accepted."""

import hashlib
import json
from collections import Counter
from decimal import Decimal

from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import Instrument, MonthlyReturn, ReturnSeries
from app.db.models.reference import ReferenceObservation, ReferenceRevision, ReferenceSeries

BIL = "BIL_RISK_FREE_USD_MONTHLY"
INFLATION = "US_INFLATION_LAZYPORTFOLIO_MONTHLY"


def bil_series(session):
    rows = list(
        session.scalars(
            select(ReturnSeries)
            .join(Instrument)
            .where(
                Instrument.source == "lazyportfolioetf",
                Instrument.ticker == "BIL",
                ReturnSeries.currency == "USD",
                ReturnSeries.validation_status == "validated",
                ReturnSeries.frequency == "monthly",
                ReturnSeries.return_type == "nominal_total_return",
            )
        )
    )
    if len(rows) != 1:
        raise ValueError(
            "Import exactly one validated LazyPortfolioETF BIL USD monthly nominal series first."
        )
    return rows[0]


def refresh(session, observations, nominal, snapshot, *, accept_revisions=False):
    bil = bil_series(session)
    stored = {
        o.date: o.return_value
        for o in session.scalars(select(MonthlyReturn).where(MonthlyReturn.series_id == bil.id))
    }
    overlap = [o for o in nominal.observations if o.date in stored]
    # Source observations are rounded percentages, not rounded cumulative capital.
    tolerance = Decimal(10) ** (-nominal.percent_decimal_places) / 100
    if not overlap or any(abs(o.return_value - stored[o.date]) > tolerance for o in overlap):
        raise ValueError(
            "BIL nominal overlap differs beyond source rounding; "
            "refresh/accept nominal revisions first."
        )
    report = {
        "observations": len(observations),
        "start": str(observations[0].month),
        "end": str(observations[-1].month),
        "statuses": dict(Counter(o.status for o in observations)),
        "snapshot": snapshot,
        "pending_revisions": 0,
    }
    series = session.scalar(select(ReferenceSeries).where(ReferenceSeries.series_code == INFLATION))
    if series is None:
        series = ReferenceSeries(
            series_code=INFLATION,
            start_date=observations[0].month,
            end_date=observations[-1].month,
            observation_count=0,
            data_version=snapshot,
            source_metadata={},
        )
        session.add(series)
        session.flush()
    existing = {
        o.month: o
        for o in session.scalars(
            select(ReferenceObservation).where(ReferenceObservation.series_id == series.id)
        )
    }
    for item in observations:
        old = existing.get(item.month)
        if old and (old.value != item.value or old.status != item.status):
            revision = session.scalar(
                select(ReferenceRevision).where(
                    ReferenceRevision.series_id == series.id,
                    ReferenceRevision.month == item.month,
                    ReferenceRevision.source_snapshot == snapshot,
                    ReferenceRevision.new_value == item.value,
                    ReferenceRevision.new_status == item.status,
                )
            )
            if revision is None:
                revision = ReferenceRevision(
                    series_id=series.id,
                    month=item.month,
                    old_value=old.value,
                    new_value=item.value,
                    old_status=old.status,
                    new_status=item.status,
                    source_snapshot=snapshot,
                )
                session.add(revision)
            provisional = old.status == "assumed_zero_unpublished" and item.status != old.status
            if not accept_revisions and not provisional:
                report["pending_revisions"] += 1
                continue
            revision.applied_at = utcnow()
        if old is None:
            old = ReferenceObservation(
                series_id=series.id,
                month=item.month,
                value=item.value,
                status=item.status,
                source_snapshot=snapshot,
            )
            session.add(old)
        elif old.value != item.value or old.status != item.status:
            old.value, old.status, old.source_snapshot = item.value, item.status, snapshot
            old.updated_at = utcnow()
    session.flush()
    rows = list(
        session.scalars(
            select(ReferenceObservation)
            .where(ReferenceObservation.series_id == series.id)
            .order_by(ReferenceObservation.month)
        )
    )
    series.data_version = hashlib.sha256(
        json.dumps(
            [[str(r.month), str(r.value), r.status, r.source_snapshot] for r in rows]
        ).encode()
    ).hexdigest()
    series.start_date, series.end_date = rows[0].month, rows[-1].month
    series.observation_count = len(rows)
    series.last_updated_at = utcnow()
    series.source_metadata = {
        "source_snapshot": snapshot,
        "nominal_overlap_count": len(overlap),
        "rounding_tolerance": str(tolerance),
        "pending_revisions": report["pending_revisions"],
    }
    pointer = session.scalar(select(ReferenceSeries).where(ReferenceSeries.series_code == BIL))
    if pointer is None:
        pointer = ReferenceSeries(series_code=BIL, source_metadata={})
        session.add(pointer)
    pointer.return_series_id = bil.id
    pointer.start_date, pointer.end_date, pointer.observation_count = (
        bil.start_date,
        bil.end_date,
        bil.observation_count,
    )
    pointer.data_version = hashlib.sha256(
        json.dumps(sorted((str(k), str(v)) for k, v in stored.items())).encode()
    ).hexdigest()
    pointer.last_updated_at = bil.last_updated_at
    return report
