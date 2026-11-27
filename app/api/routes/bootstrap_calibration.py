from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.analytics.bootstrap_calibration import calibration_preview
from app.analytics.data_loader import attach_references, load_returns
from app.analytics.jobs import JobCapacityError
from app.analytics.models import AnalyticsError
from app.api.dependencies import get_session
from app.schemas.bootstrap_calibration import (
    CalibrationDetail,
    CalibrationHoldoutRequest,
    CalibrationPreview,
    CalibrationRequest,
)
from app.schemas.frontier import JobRead

router = APIRouter(prefix="/api/v1/analytics", tags=["bootstrap calibration"])
DatabaseSession = Annotated[Session, Depends(get_session)]


def calibration_panel(body, session):
    panel = load_returns(
        session, body.instrument_ids, start_date=body.start_date, end_date=body.end_date
    )
    for name, requested, index in (
        ("start", body.start_date, 0),
        ("end", body.end_date, -1),
    ):
        if requested and requested.strftime("%Y-%m") != str(panel.returns.index[index]):
            panel.warnings.append(
                f"Requested {name} month {requested:%Y-%m}. "
                f"The common history uses {panel.returns.index[index]}."
            )
    panel = attach_references(
        session,
        panel,
        return_basis=body.return_basis,
        risk_free_source="fixed_annual",
        risk_free_rate=0,
    )
    inflation = panel.reference_metadata.get("inflation_observations", [])
    unavailable = [month for month, _, status in inflation if status.startswith("assumed_zero")]
    if unavailable:
        raise AnalyticsError(
            "Calibration requires published or reconstructed inflation. "
            f"Select an end date before unpublished months: {', '.join(unavailable)}."
        )
    return panel


@router.post("/bootstrap-calibration/preview", response_model=CalibrationPreview)
def preview_calibration(body: CalibrationRequest, session: DatabaseSession) -> dict:
    try:
        return calibration_preview(calibration_panel(body, session), body.settings())
    except AnalyticsError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/bootstrap-calibration", response_model=JobRead, status_code=202)
def submit_calibration(
    body: CalibrationRequest, request: Request, session: DatabaseSession
) -> dict:
    try:
        panel = calibration_panel(body, session)
        settings = body.settings()
        preview = calibration_preview(panel, settings)
        if not preview["ready"]:
            raise AnalyticsError(preview["blocking_reason"])
        return request.app.state.analytics_jobs.submit(
            panel,
            "bootstrap-calibration",
            {"settings": settings, "_api_request": body.model_dump(mode="json")},
        )
    except AnalyticsError as exc:
        raise HTTPException(422, str(exc)) from exc
    except JobCapacityError as exc:
        raise HTTPException(429, str(exc)) from exc


@router.post("/bootstrap-calibration-holdout", response_model=JobRead, status_code=202)
def submit_holdout(body: CalibrationHoldoutRequest, request: Request) -> dict:
    try:
        return request.app.state.analytics_jobs.submit_calibration_holdout(body.calibration_job_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except JobCapacityError as exc:
        raise HTTPException(429, str(exc)) from exc


@router.get("/bootstrap-calibration/{job_id}/detail", response_model=CalibrationDetail)
def distribution_detail(
    job_id: str,
    request: Request,
    fold: int,
    horizon: int,
    metrics: Annotated[list[str], Query(min_length=1, max_length=12)],
    member: Annotated[list[str], Query(min_length=1, max_length=2)],
    lengths: Annotated[list[int], Query(min_length=1, max_length=24)],
) -> dict:
    try:
        return request.app.state.analytics_jobs.calibration_detail(
            job_id, fold, horizon, metrics, tuple(member), lengths
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/bootstrap-calibration/{job_id}", response_model=JobRead)
def cancel_calibration(job_id: str, request: Request) -> dict:
    job = request.app.state.analytics_jobs.cancel_calibration(job_id)
    if not job:
        raise HTTPException(404, "Calibration job not found or expired.")
    return job
