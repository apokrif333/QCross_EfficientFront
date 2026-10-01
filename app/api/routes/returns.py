from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.db.models import MonthlyReturn, ReturnSeries
from app.schemas.returns import MonthlyReturnRead, ReturnSeriesRead

router = APIRouter(prefix="/api/v1/return-series", tags=["returns"])
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[ReturnSeriesRead])
def return_series(
    session: DatabaseSession,
    instrument_id: int | None = None,
    source: str | None = None,
    currency: Annotated[str | None, Query(pattern="^[A-Za-z]{3}$")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReturnSeries]:
    query = select(ReturnSeries)
    if instrument_id is not None:
        query = query.where(ReturnSeries.instrument_id == instrument_id)
    if source:
        query = query.where(ReturnSeries.source == source)
    if currency:
        query = query.where(ReturnSeries.currency == currency.upper())
    return list(session.scalars(query.order_by(ReturnSeries.id).limit(limit).offset(offset)))


@router.get("/{series_id}", response_model=ReturnSeriesRead)
def series_by_id(series_id: int, session: DatabaseSession) -> ReturnSeries:
    row = session.get(ReturnSeries, series_id)
    if row is None:
        raise HTTPException(404, "Return series not found")
    return row


@router.get("/{series_id}/observations", response_model=list[MonthlyReturnRead])
def monthly_observations(
    series_id: int,
    session: DatabaseSession,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 10000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MonthlyReturn]:
    if session.get(ReturnSeries, series_id) is None:
        raise HTTPException(404, "Return series not found")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "start_date must not exceed end_date")
    query = select(MonthlyReturn).where(MonthlyReturn.series_id == series_id)
    if start_date:
        query = query.where(MonthlyReturn.date >= start_date)
    if end_date:
        query = query.where(MonthlyReturn.date <= end_date)
    return list(session.scalars(query.order_by(MonthlyReturn.date).limit(limit).offset(offset)))
