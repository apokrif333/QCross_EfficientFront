from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analytics.bootstrap_calibration import METHODOLOGY
from app.analytics.calibration_metrics import HORIZONS


class CalibrationProgress(BaseModel):
    phase: Literal["starting", "reference", "scoring", "summarizing", "transferring", "completed"]
    completed_steps: int = Field(ge=0)
    total_steps: int | None = Field(default=None, gt=0)
    fold_index: int | None = Field(default=None, gt=0)
    total_folds: int | None = Field(default=None, gt=0)
    block_length: int | None = Field(default=None, gt=0)


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    instrument_ids: list[int] = Field(min_length=2, max_length=15)
    start_date: date | None = None
    end_date: date | None = None
    return_basis: Literal["nominal", "real"] = "nominal"
    train_length: int = Field(default=240, ge=24, le=1200)
    test_length: int = Field(default=240, ge=3, le=1200)
    step: int | None = Field(default=None, ge=3, le=1200)
    generations: int = Field(default=1000, ge=2, le=2000)
    min_block_length: int = Field(default=3, ge=1, le=24)
    max_block_length: int = Field(default=24, ge=1, le=24)
    block_length_step: int = Field(default=1, ge=1, le=23)
    random_seed: int = Field(default=42, ge=0, le=2**32 - 1)
    reference_block_length: int = Field(default=12, ge=1, le=120)
    practical_tolerance: float = Field(default=0.05, ge=0, le=1)
    final_holdout: bool = True
    horizons: list[Literal[3, 6, 12, 24, 60, 120, 240]] = Field(
        default_factory=lambda: list(HORIZONS), min_length=1, max_length=7
    )

    @model_validator(mode="after")
    def check_consistency(self):
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("Choose distinct instruments.")
        if self.min_block_length > self.max_block_length:
            raise ValueError("Minimum block length cannot exceed the maximum.")
        if self.step is not None and self.step < self.test_length:
            raise ValueError("Step cannot be shorter than Test.")
        if len(set(self.horizons)) != len(self.horizons):
            raise ValueError("Choose distinct horizons.")
        if max(self.horizons) > min(self.train_length, self.test_length):
            raise ValueError("Selected horizons must fit inside both Train and Test.")
        return self

    def settings(self):
        settings = self.model_dump(
            exclude={"instrument_ids", "start_date", "end_date", "return_basis"}
        )
        settings["horizons"].sort()
        settings["step"] = settings["step"] or self.test_length
        return settings


class CalibrationHoldoutRequest(BaseModel):
    calibration_job_id: str


class CalibrationPeriod(BaseModel):
    start: str
    end: str
    months: int


class CalibrationFoldRead(BaseModel):
    fold: int
    train: CalibrationPeriod
    test: CalibrationPeriod
    holdout: bool
    intervals: dict[str, int]


class CalibrationPreview(BaseModel):
    ready: bool
    blocking_reason: str | None
    history: CalibrationPeriod
    folds: list[CalibrationFoldRead]
    holdout: CalibrationFoldRead | None
    full_calibration_folds: int
    horizons: list[int]
    lengths: list[int]
    interval_counts: dict[str, int]
    generations: int
    trajectory_count: int
    simulated_asset_months: int
    warnings: list[str]
    assets: list[dict[str, Any]]


class CalibrationRankingRow(BaseModel):
    length: int
    score: float
    relative_difference: float | None
    comparable: bool
    energy: float
    temporal_returns: float
    temporal_absolute: float
    cross_asset: float


class CalibrationForecast(BaseModel):
    min: float
    max: float
    p05: float
    p25: float
    median: float
    p75: float
    p95: float
    mean: float
    std: float
    histogram: list[int]
    undefined: int
    crps: list[float | None]
    mean_crps: float | None
    ranks: list[float | None]


class CalibrationMetricDetail(BaseModel):
    scale: float
    scale_method: Literal["IQR", "SD", "minimum"]
    reference_count: int
    train: list[float | None]
    actual: list[float | None]
    series: dict[str, CalibrationForecast]


class CalibrationDetail(BaseModel):
    intervals: list[CalibrationPeriod]
    metrics: dict[str, CalibrationMetricDetail]


class CalibrationResult(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    methodology: Literal[METHODOLOGY]
    phase: Literal["calibration", "holdout"]
    optimal_length: int
    minimum_score: float
    holdout_status: Literal["available", "unavailable", "evaluated", "previously_viewed"]
    preview: CalibrationPreview
    ranking: list[CalibrationRankingRow]
    horizon_weights: dict[str, float]
    group_weights: dict[str, float]
    by_horizon: dict[str, dict[str, float | None]]
    by_fold: dict[str, dict[str, float | None]]
    by_group: dict[str, dict[str, float | None]]
    by_metric: dict[str, dict[str, float | None]]
    reproducibility: dict[str, Any]
