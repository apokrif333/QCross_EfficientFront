from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analytics.constraints import user_allocation
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
    return_basis: Literal["nominal", "real"] = "nominal"
    risk_free_source: Literal["historical_bil", "fixed_annual"] = "historical_bil"
    risk_free_rate: float = Field(default=0.03, gt=-1, le=1)
    transaction_fee_rate: float = Field(default=0.0, ge=0, lt=1)
    rebalancing_interval_months: int | None = Field(default=12, ge=1, le=1200)
    objective: Objective = "gmv"
    target_return: float | None = Field(default=None, ge=-12, le=12)
    asset_constraints: dict[str, WeightBounds] = Field(default_factory=dict, max_length=15)
    group_constraints: dict[str, WeightBounds] = Field(default_factory=dict, max_length=30)
    asset_groups: dict[str, Annotated[int, Field(strict=True, ge=1, le=5)]] = Field(
        default_factory=dict, max_length=15
    )
    user_weights: dict[str, float] | None = Field(default=None, max_length=15)
    include_efficiency_robustness: bool = True
    include_paired_comparison: bool = True
    rolling_window_months: int = Field(default=120, ge=120, le=10000)
    rolling_step_months: int = Field(default=12, ge=1, le=120)
    frontier_points: int = Field(default=51, ge=2, le=201)
    cv_folds: int = Field(default=4, strict=True, ge=2, le=20, multiple_of=2)
    bootstrap_iterations: int = Field(default=1000, ge=1, le=2000)
    expected_block_length: float = Field(default=12, ge=3, le=24)
    random_seed: int = Field(default=42, ge=0, le=2**32 - 1)
    bootstrap_objectives: (
        list[Literal["gmv", "max_sharpe", "target_return", "max_return_at_user_risk"]] | None
    ) = Field(default=None, min_length=1, max_length=4)
    risk_aversions: list[float] | None = Field(default=None, min_length=2, max_length=201)

    @model_validator(mode="before")
    @classmethod
    def legacy_rate(cls, value):
        if (
            isinstance(value, dict)
            and "risk_free_source" not in value
            and "risk_free_rate" in value
        ):
            value = {**value, "risk_free_source": "fixed_annual"}
        return value

    @model_validator(mode="after")
    def valid_selection(self):
        assets = list(map(str, self.instrument_ids))
        if set(self.asset_groups) - set(assets) or any(
            not 1 <= group <= 5 for group in self.asset_groups.values()
        ):
            raise ValueError("Groups require selected instrument IDs and numbers from 1 to 5.")
        allocation = user_allocation(assets, self.user_weights)
        if (
            self.objective == "max_return_at_user_risk"
            or "max_return_at_user_risk" in (self.bootstrap_objectives or [])
        ) and allocation is None:
            raise ValueError("My Portfolio requires explicit user_weights.")
        if any(i <= 0 for i in self.instrument_ids) or len(set(self.instrument_ids)) != len(
            self.instrument_ids
        ):
            raise ValueError("Select 2 to 15 distinct positive instrument IDs.")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date.")
        if self.uses_target_return() and self.target_return is None:
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

    def uses_target_return(self) -> bool:
        return self.objective == "target_return" or "target_return" in (
            self.bootstrap_objectives or []
        )

    def estimator_options(self) -> dict:
        return {
            "transaction_fee_rate": self.transaction_fee_rate,
            "rebalancing_interval_months": self.rebalancing_interval_months,
            "covariance_method": self.covariance_method,
            "shrinkage_target": self.shrinkage_target,
            "cv_folds": self.cv_folds,
            "asset_constraints": {k: v.model_dump() for k, v in self.asset_constraints.items()},
            "group_constraints": {k: v.model_dump() for k, v in self.group_constraints.items()},
        }


class ResampledFrontierRequest(AnalyticsRequest):
    bootstrap_iterations: int = Field(default=250, ge=1, le=500)

    def uses_target_return(self) -> bool:
        # The global selection is retained in provenance, not used by this curve.
        return False


class RebalancingOptimizationRequest(AnalyticsRequest):
    def uses_target_return(self) -> bool:
        return False

    @model_validator(mode="after")
    def explicit_portfolio(self):
        if user_allocation(list(map(str, self.instrument_ids)), self.user_weights) is None:
            raise ValueError("Rebalancing optimization requires explicit user_weights.")
        return self


class RollingEfficiencyRequest(AnalyticsRequest):
    def uses_target_return(self) -> bool:
        return False


class PortfolioMetrics(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    gross_historical_metrics: dict[str, float | None] | None = None
    trading_costs: dict[str, float | int] | None = None
    expected_return: float
    volatility: float
    historical_volatility: float
    sharpe_ratio: float | None
    historical_sharpe_ratio: float | None = None
    risk_free_mean_return: float | None = None
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
    return_basis: Literal["nominal", "real"]
    risk_free_source: Literal["historical_bil", "fixed_annual"]
    reference_metadata: dict[str, Any]
    risk_free_mean_return: float
    differential_covariance_parameters: dict[str, Any]
    differential_covariance_diagnostics: dict[str, Any]
    annual_differential_covariance: list[list[float]] | None
    annual_differential_mean: list[float] | None
    status: Literal["complete", "incomplete"]
    assets: list[str]
    asset_portfolios: dict[str, PortfolioRead]
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
    reproducibility: dict[str, Any]


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
    user_portfolio: PortfolioRead | None
    frontier: list[PortfolioRead]


class FoldRead(BaseModel):
    model_config = ConfigDict(extra="allow")
    fold: int
    training_direction: Literal["train_before_validation", "train_after_validation"]
    status: str
    training: dict[str, Any]
    validation: dict[str, Any]
    weights: dict[str, float] | None
    diagnostics: dict[str, Any]


class CrossValidationResult(AnalyticsResultBase):
    cv_folds: int
    methodology: Literal["bidirectional_one_sided_block_kfold"]
    total_observations: int
    validation_observations: int
    validation_coverage_fraction: float
    minimum_portfolio_train: int
    minimum_portfolio_validation: int
    minimum_covariance_train: int
    minimum_covariance_validation: int
    validation_interpretation: str
    ensemble_evaluation: str
    original: PortfolioRead
    folds: list[FoldRead]
    ensemble: PortfolioRead | None
    stability: dict[str, Any] | None


class BootstrapObjectiveRead(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    computational_status: str | None = None
    target_return: float | None = None
    target_feasibility_rate: float | None = None
    target_feasible_iterations: int | None = None
    target_infeasible_iterations: int | None = None
    numerical_failure_iterations: int | None = None
    feasibility_unknown_iterations: int | None = None
    original_target_feasible: bool | None = None
    statistics_condition: str | None = None
    averaging_policy: str | None = None
    resampled_target_evaluation: dict[str, Any] | None = None
    requested_iterations: int
    successful_iterations: int
    failed_iterations: int
    failure_reasons: dict[str, int]
    failures: list[dict[str, Any]]
    original: PortfolioRead
    resampled: PortfolioRead | None
    stability: dict[str, Any] | None
    iterations: list[dict[str, Any]]


class BootstrapMetricDistribution(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    mean: float | None
    median: float | None
    p5: float | None
    p95: float | None
    minimum: float | None
    maximum: float | None
    valid_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    horizon_months: int = Field(gt=0)
    horizon_years: float = Field(gt=0)
    annualized: bool
    units: str


class BootstrapEfficiencyRead(BaseModel):
    model_config = ConfigDict(extra="allow")
    user_weights: dict[str, float]
    valid_count: int
    failed_count: int
    results: list[dict[str, Any]]
    horizon: dict[str, Any] | None = None
    distributions: dict[str, BootstrapMetricDistribution] | None = None


class BootstrapResult(AnalyticsResultBase):
    conditional_bootstrap: bool
    sample_fingerprint: str
    sample_length: int
    objectives: dict[str, BootstrapObjectiveRead]
    efficiency_robustness: BootstrapEfficiencyRead | None = None


class ResampledFrontierResult(AnalyticsResultBase):
    requested_iterations: int
    successful_iterations: int
    failed_iterations: int
    failure_reasons: dict[str, int]
    failures: list[dict[str, Any]]
    risk_aversions: list[float]
    frontier: list[PortfolioRead]
    conditional_bootstrap: bool


class RollingEfficiencyResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Literal["complete", "incomplete"]
    methodology: str
    windows: list[dict[str, Any]]
    summary: dict[str, Any]
    user_weights: dict[str, float]
    reproducibility: dict[str, Any]


class RebalancingOptimizationResult(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    status: Literal["complete"]
    methodology: Literal["historical_in_sample_rebalancing_comparison_v1"]
    assets: list[str]
    user_weights: dict[str, float]
    transaction_fee_rate: float
    strategies: list[dict[str, Any]] = Field(min_length=20, max_length=20)
    period: dict[str, Any]
    reproducibility: dict[str, Any]


class JobRead(BaseModel):
    job_id: str
    kind: Literal[
        "frontier",
        "cross-validation",
        "bootstrap",
        "resampled-frontier",
        "rolling-efficiency",
        "rebalancing-optimization",
    ]
    status: Literal["running", "completed", "failed", "timed_out"]
    submitted_at: str
    elapsed_seconds: float
    timeout_seconds: float
    status_url: str
    result_url: str
    error: str | None = None


class JobResultRead(JobRead):
    result: (
        FrontierResult
        | CrossValidationResult
        | BootstrapResult
        | ResampledFrontierResult
        | RollingEfficiencyResult
        | RebalancingOptimizationResult
        | None
    )
