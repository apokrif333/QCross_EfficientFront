from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.db.models import Instrument
from app.repositories.instruments import list_instruments
from app.schemas.instrument import InstrumentRead

router = APIRouter(prefix="/api/v1/instruments", tags=["instruments"])
DatabaseSession = Annotated[Session, Depends(get_session)]
CurrencyFilter = Annotated[str | None, Query(pattern="^[A-Za-z]{3}$")]


@router.get("", response_model=list[InstrumentRead])
def instruments(
    session: DatabaseSession,
    source: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    search: str | None = None,
    currency: CurrencyFilter = None,
    available_currency: CurrencyFilter = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Instrument]:
    return list_instruments(
        session,
        source=source,
        category=category,
        active=active,
        search=search,
        currency=currency,
        available_currency=available_currency,
        limit=limit,
        offset=offset,
    )


@router.get("/{instrument_id}", response_model=InstrumentRead)
def instrument_by_id(instrument_id: int, session: DatabaseSession) -> Instrument:
    row = session.get(Instrument, instrument_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return row
