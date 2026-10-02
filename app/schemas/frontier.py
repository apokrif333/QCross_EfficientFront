from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analytics.models import CovarianceMethod, Objective, ShrinkageTarget


class WeightBounds(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    min: float = Field(default=0, ge=0, le=1)
    max: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.min > self.max:
            raise ValueError("Weight bounds must satisfy min <= max.")
        return self


class AnalyticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    instrument_ids: list[int] = Field(min_length=2, max_length=15)
    currency: Literal["USD"] = "USD"
    start_date: date | None = None
    end_date: date | None = None
    covariance_method: CovarianceMethod = "sample"
    shrinkage_target: ShrinkageTarget = "identity"
    risk_free_rate: float = Field(default=0.03, ge=-1, le=1)
    objective: Objective = "gmv"
    target_return: float | None = Field(default=None, ge=-12, le=12)
    asset_constraints: dict[str, WeightBounds] = Field(default_factory=dict, max_length=15)
    group_constraints: dict[str, WeightBounds] = Field(default_factory=dict, max_length=30)
    frontier_points: int = Field(default=51, ge=2, le=201)
    cv_folds: int = Field(default=5, ge=2, le=10)
    bootstrap_iterations: int = Field(default=1000, ge=1, le=2000)
    expected_block_length: float = Field(default=12, ge=3, le=24)
    random_seed: int = Field(default=42, ge=0, le=2**32 - 1)
    bootstrap_objectives: list[Literal["gmv", "max_sharpe"]] | None = Field(
        default=None, min_length=1, max_length=2
    )
    risk_aversions: list[float] | None = Field(default=None, min_length=2, max_length=201)

    @model_validator(mode="after")
    def valid_selection(self):
        if any(i <= 0 for i in self.instrument_ids) or len(set(self.instrument_ids)) != len(
            self.instrument_ids
        ):
            raise ValueError("Select 2 to 15 distinct positive instrument IDs.")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date.")
        if self.objective == "target_return" and self.target_return is None:
            raise ValueError("The target_return objective requires an annual target_return.")
        if self.bootstrap_objectives and len(set(self.bootstrap_objectives)) != len(
            self.bootstrap_objectives
        ):
            raise ValueError("Bootstrap objectives must be distinct.")
        if self.risk_aversions is not None:
            if len(self.risk_aversions) != self.frontier_points or any(
                not 0 < gamma <= 1e6 for gamma in self.risk_aversions
            ):
                raise ValueError("Supply one risk aversion in (0, 1e6] per frontier point.")
        return self

    def estimator_options(self) -> dict:
        return {
            "covariance_method": self.covariance_method,
            "shrinkage_target": self.shrinkage_target,
            "cv_folds": self.cv_folds,
            "asset_constraints": {k: v.model_dump() for k, v in self.asset_constraints.items()},
            "group_constraints": {k: v.model_dump() for k, v in self.group_constraints.items()},
        }


class ResampledFrontierRequest(AnalyticsRequest):
    bootstrap_iterations: int = Field(default=250, ge=1, le=500)


class PortfolioMetrics(BaseModel):
    expected_return: float
    volatility: float
    sharpe_ratio: float | None
    historical_cagr: float
    historical_max_drawdown: float


class PortfolioRead(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    weights: dict[str, float] | None
    metrics: PortfolioMetrics | None
    diagnostics: dict[str, Any]


class AnalyticsResultBase(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Literal["complete", "incomplete"]
    assets: list[str]
    currency: Literal["USD"]
    covariance_method: CovarianceMethod
    covariance_parameters: dict[str, Any]
    covariance_diagnostics: dict[str, Any]
    risk_free_rate: float
    warnings: list[str]
    execution_seconds: float
    period: dict[str, Any]
    annual_expected_returns: dict[str, float]
    annual_covariance: list[list[float]]
    correlation: list[list[float | None]]


class FrontierResult(AnalyticsResultBase):
    period: dict[str, Any]
    annual_expected_returns: dict[str, float]
    annual_covariance: list[list[float]]
    correlation: list[list[float | None]]
    gmv: PortfolioRead
    max_sharpe: PortfolioRead
    maximum_return: PortfolioRead
    selected_portfolio: PortfolioRead
    equal_weight: PortfolioRead
    frontier: list[PortfolioRead]


class FoldRead(BaseModel):
    model_config = ConfigDict(extra="allow")
    fold: int
    status: str
    training: dict[str, Any]
    validation: dict[str, Any]
    weights: dict[str, float] | None
    diagnostics: dict[str, Any]


class CrossValidationResult(AnalyticsResultBase):
    cv_folds: int
    ensemble_evaluation: str
    original: PortfolioRead
    folds: list[FoldRead]
    ensemble: PortfolioRead | None
    stability: dict[str, Any] | None


class BootstrapObjectiveRead(BaseModel):
    status: str
    requested_iterations: int
    successful_iterations: int
    failed_iterations: int
    failure_reasons: dict[str, int]
    failures: list[dict[str, Any]]
    original: PortfolioRead
    resampled: PortfolioRead | None
    stability: dict[str, Any] | None
    iterations: list[dict[str, Any]]


class BootstrapResult(AnalyticsResultBase):
    conditional_bootstrap: bool
    sample_fingerprint: str
    sample_length: int
    objectives: dict[str, BootstrapObjectiveRead]


class ResampledFrontierResult(AnalyticsResultBase):
    requested_iterations: int
    successful_iterations: int
    failed_iterations: int
    failure_reasons: dict[str, int]
    failures: list[dict[str, Any]]
    risk_aversions: list[float]
    frontier: list[PortfolioRead]
    conditional_bootstrap: bool


class JobRead(BaseModel):
    job_id: str
    kind: Literal["frontier", "cross-validation", "bootstrap", "resampled-frontier"]
    status: Literal["running", "completed", "failed", "timed_out"]
    submitted_at: str
    elapsed_seconds: float
    timeout_seconds: float
    status_url: str
    result_url: str
    error: str | None = None


class JobResultRead(JobRead):
    result: (
        FrontierResult | CrossValidationResult | BootstrapResult | ResampledFrontierResult | None
    )
