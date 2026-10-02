from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.analytics.constraints import build_constraints
from app.analytics.cross_validation import portfolio_folds
from app.analytics.data_loader import load_returns
from app.analytics.jobs import JobCapacityError
from app.analytics.models import AnalyticsError
from app.api.dependencies import get_session
from app.schemas.frontier import AnalyticsRequest, JobRead, JobResultRead, ResampledFrontierRequest

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
DatabaseSession = Annotated[Session, Depends(get_session)]


def _submit(body: AnalyticsRequest, request: Request, session: Session, kind: str) -> dict:
    try:
        panel = load_returns(
            session,
            body.instrument_ids,
            currency=body.currency,
            start_date=body.start_date,
            end_date=body.end_date,
        )
        options = body.estimator_options()
        build_constraints(
            panel.assets, options["asset_constraints"], options["group_constraints"], panel.groups
        )
        options["risk_free_rate"] = body.risk_free_rate
        if kind == "frontier":
            options.update(
                frontier_points=body.frontier_points,
                objective=body.objective,
                target_return=body.target_return,
            )
        elif kind == "cross-validation":
            portfolio_folds(len(panel.values), body.cv_folds)
            options.update(objective=body.objective, target_return=body.target_return)
        else:
            if body.objective == "target_return":
                raise AnalyticsError(
                    "Bootstrap supports GMV and Max Sharpe, not target-return optimization."
                )
            options.update(
                bootstrap_iterations=body.bootstrap_iterations,
                expected_block_length=body.expected_block_length,
                random_seed=body.random_seed,
            )
            if kind == "bootstrap":
                options["objectives"] = body.bootstrap_objectives or (
                    [body.objective]
                    if "objective" in body.model_fields_set
                    else ["gmv", "max_sharpe"]
                )
            else:
                if body.frontier_points * body.bootstrap_iterations > 30000:
                    raise AnalyticsError(
                        "Resampled frontier is limited to 30,000 portfolio optimizations per job."
                    )
                options.update(
                    frontier_points=body.frontier_points, risk_aversions=body.risk_aversions
                )
        return request.app.state.analytics_jobs.submit(panel, kind, options)
    except AnalyticsError as exc:
        raise HTTPException(422, str(exc)) from exc
    except JobCapacityError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "5"}) from exc


@router.post("/frontier", response_model=JobRead, status_code=202)
def frontier(body: AnalyticsRequest, request: Request, session: DatabaseSession) -> dict:
    return _submit(body, request, session, "frontier")


@router.post("/cross-validation", response_model=JobRead, status_code=202)
def cross_validation(body: AnalyticsRequest, request: Request, session: DatabaseSession) -> dict:
    return _submit(body, request, session, "cross-validation")


@router.post("/bootstrap", response_model=JobRead, status_code=202)
def bootstrap(body: AnalyticsRequest, request: Request, session: DatabaseSession) -> dict:
    return _submit(body, request, session, "bootstrap")


@router.post("/resampled-frontier", response_model=JobRead, status_code=202)
def resampled_frontier(
    body: ResampledFrontierRequest, request: Request, session: DatabaseSession
) -> dict:
    return _submit(body, request, session, "resampled-frontier")


@router.get("/jobs/{job_id}", response_model=JobRead)
def job_status(job_id: str, request: Request) -> dict:
    job = request.app.state.analytics_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Analytics job not found or expired.")
    return job


@router.get("/jobs/{job_id}/result", response_model=JobResultRead)
def job_result(job_id: str, request: Request) -> dict:
    job = request.app.state.analytics_jobs.get(job_id, include_result=True)
    if job is None:
        raise HTTPException(404, "Analytics job not found or expired.")
    return job
