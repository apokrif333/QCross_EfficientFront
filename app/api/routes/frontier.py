from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.constraints import build_constraints, numbered_groups, user_allocation
from app.analytics.data_loader import attach_references, instrument_groups, load_returns
from app.analytics.jobs import JobCapacityError
from app.analytics.models import AnalyticsError
from app.analytics.provenance import engine_identity, panel_provenance
from app.analytics.splitting import (
    MIN_COVARIANCE_TRAIN,
    MIN_COVARIANCE_VALIDATION,
    MIN_PORTFOLIO_TRAIN,
    MIN_PORTFOLIO_VALIDATION,
    eligible_folds,
    validate_cv_configuration,
)
from app.api.dependencies import get_session
from app.db.models import Instrument, ReturnSeries
from app.schemas.frontier import (
    AnalyticsRequest,
    JobRead,
    JobResultRead,
    RebalancingOptimizationRequest,
    ResampledFrontierRequest,
    RollingEfficiencyRequest,
)

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
        from app.analytics.control_loader import load_control_panel, transform_control_panel

        panel = load_control_panel(session, panel, body.portfolio_controls.model_dump())
        panel = attach_references(
            session,
            panel,
            return_basis=body.return_basis,
            risk_free_source=body.risk_free_source,
            risk_free_rate=body.risk_free_rate,
        )
        transform_control_panel(panel)
        options = body.estimator_options()
        panel = numbered_groups(panel, body.asset_groups)
        constraints = build_constraints(
            panel.assets, options["asset_constraints"], options["group_constraints"], panel.groups
        )
        if body.objective == "max_return_at_user_risk" or "max_return_at_user_risk" in (
            body.bootstrap_objectives or []
        ):
            allocation = user_allocation(panel.assets, body.user_weights)
            if allocation is None:
                raise AnalyticsError("My Portfolio requires explicit user_weights.")
            constraints.require_feasible(allocation)
        options["risk_free_rate"] = body.risk_free_rate
        if kind not in {"rolling-efficiency", "rebalancing-optimization"}:
            validate_cv_configuration(
                len(panel.values),
                body.cv_folds,
                portfolio=kind == "cross-validation",
                covariance_method=body.covariance_method,
            )
        if kind == "rebalancing-optimization":
            allocation = user_allocation(panel.assets, body.user_weights)
            if allocation is None:
                raise AnalyticsError("Rebalancing optimization requires explicit user_weights.")
            constraints.require_feasible(allocation)
            options["user_weights"] = body.user_weights
        elif kind == "frontier":
            options.update(
                frontier_points=body.frontier_points,
                objective=body.objective,
                target_return=body.target_return,
                user_weights=body.user_weights,
            )
        elif kind == "cross-validation":
            options.update(
                objective=body.objective,
                target_return=body.target_return,
                user_weights=body.user_weights,
                include_efficiency_robustness=body.include_efficiency_robustness,
                include_paired_comparison=body.include_paired_comparison,
            )
        elif kind == "rolling-efficiency":
            options.pop("transaction_fee_rate", None)
            options.pop("rebalancing_interval_months", None)
            if user_allocation(panel.assets, body.user_weights) is None:
                raise AnalyticsError("Rolling efficiency requires explicit user_weights.")
            if len(panel.values) < body.rolling_window_months:
                raise AnalyticsError(
                    f"Rolling window requires {body.rolling_window_months} common months; "
                    f"only {len(panel.values)} are available."
                )
            options.update(
                user_weights=body.user_weights,
                window_months=body.rolling_window_months,
                step_months=body.rolling_step_months,
            )
        else:
            options.update(
                bootstrap_iterations=body.bootstrap_iterations,
                expected_block_length=body.expected_block_length,
                random_seed=body.random_seed,
            )
            if kind == "bootstrap":
                if (
                    body.user_weights
                    and body.include_paired_comparison
                    and body.bootstrap_iterations % 2
                ):
                    raise AnalyticsError(
                        "Paired Bootstrap requires an even number of generated datasets."
                    )
                options["include_paired_comparison"] = body.include_paired_comparison
                options["target_return"] = body.target_return
                options["user_weights"] = body.user_weights
                options["include_efficiency_robustness"] = body.include_efficiency_robustness
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
                    frontier_points=body.frontier_points,
                    risk_aversions=body.risk_aversions,
                    user_weights=body.user_weights,
                )
        options["_api_request"] = body.model_dump(mode="json")
        if kind == "bootstrap":
            # Preserve the effective default when this full request is replayed.
            options["_api_request"]["bootstrap_objectives"] = options["objectives"]
        return request.app.state.analytics_jobs.submit(panel, kind, options)
    except AnalyticsError as exc:
        raise HTTPException(422, str(exc)) from exc
    except JobCapacityError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "5"}) from exc


@router.get("/catalog")
def analytics_catalog(session: DatabaseSession) -> dict:
    rows = session.execute(
        select(Instrument, ReturnSeries)
        .join(ReturnSeries, ReturnSeries.instrument_id == Instrument.id)
        .where(
            ReturnSeries.currency == "USD",
            ReturnSeries.validation_status == "validated",
            ReturnSeries.frequency == "monthly",
            ReturnSeries.return_type == "nominal_total_return",
        )
        .order_by(Instrument.id)
    ).all()
    counts = {}
    for instrument, _series in rows:
        counts[instrument.id] = counts.get(instrument.id, 0) + 1
    return {
        "engine": engine_identity(),
        "instruments": [
            {
                "id": instrument.id,
                "ticker": instrument.ticker,
                "name": instrument.name,
                "category": instrument.category,
                "groups": instrument_groups(instrument),
                "currency": "USD",
                "series_id": series.id,
                "start_date": str(series.start_date),
                "end_date": str(series.end_date),
                "observation_count": series.observation_count,
                "last_updated_at": series.last_updated_at.isoformat(),
                "is_active": instrument.is_active,
            }
            for instrument, series in rows
            if counts[instrument.id] == 1
        ],
    }


@router.post("/data-preview")
def data_preview(body: AnalyticsRequest, session: DatabaseSession) -> dict:
    try:
        panel = load_returns(
            session,
            body.instrument_ids,
            currency=body.currency,
            start_date=body.start_date,
            end_date=body.end_date,
        )
        from app.analytics.control_loader import load_control_panel, transform_control_panel

        panel = load_control_panel(session, panel, body.portfolio_controls.model_dump())
        panel = attach_references(
            session,
            panel,
            return_basis=body.return_basis,
            risk_free_source=body.risk_free_source,
            risk_free_rate=body.risk_free_rate,
        )
        transform_control_panel(panel)
        return {
            "return_basis": panel.return_basis,
            "risk_free_source": panel.risk_free_source,
            "reference_metadata": panel.reference_metadata,
            "risk_free_mean_return": float(panel.risk_free_returns.mean() * 12)
            if panel.risk_free_returns is not None
            else body.risk_free_rate,
            "assets": panel.assets,
            "period": {
                "start": str(panel.returns.index[0]),
                "end": str(panel.returns.index[-1]),
                "observations": len(panel.values),
            },
            "groups": panel.groups,
            "source_metadata": panel.metadata,
            "warnings": panel.warnings,
            "eligible_portfolio_folds": eligible_folds(
                len(panel.values),
                min_train=MIN_PORTFOLIO_TRAIN,
                min_validation=MIN_PORTFOLIO_VALIDATION,
            ),
            "eligible_covariance_folds": eligible_folds(
                len(panel.values),
                min_train=MIN_COVARIANCE_TRAIN,
                min_validation=MIN_COVARIANCE_VALIDATION,
            ),
            "reproducibility": panel_provenance(panel),
        }
    except AnalyticsError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/frontier", response_model=JobRead, status_code=202)
def frontier(body: AnalyticsRequest, request: Request, session: DatabaseSession) -> dict:
    return _submit(body, request, session, "frontier")


@router.post("/rebalancing-optimization", response_model=JobRead, status_code=202)
def rebalancing_optimization(
    body: RebalancingOptimizationRequest, request: Request, session: DatabaseSession
) -> dict:
    return _submit(body, request, session, "rebalancing-optimization")


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


@router.post("/rolling-efficiency", response_model=JobRead, status_code=202)
def rolling_efficiency(
    body: RollingEfficiencyRequest, request: Request, session: DatabaseSession
) -> dict:
    return _submit(body, request, session, "rolling-efficiency")


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
