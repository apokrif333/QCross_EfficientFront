from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DownsideSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    target_annual: float = Field(gt=0, le=10)
    lookback_months: int = Field(strict=True, ge=3, le=24)


class TrendRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator: Literal["disabled", "sma", "momentum"] = "disabled"
    lookback_months: int = Field(default=12, strict=True, ge=2, le=36)


class TrendSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: Literal["remaining", "hedge"] = "remaining"
    asset_rules: dict[str, TrendRule] = Field(default_factory=dict, max_length=15)
    sensitivity_asset: str | None = None


class PortfolioControls(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["none", "downside_vol", "trend"] = "none"
    hedge_weights: dict[str, float] = Field(default_factory=dict, max_length=15)
    downside_vol: DownsideSettings | None = None
    trend: TrendSettings | None = None

    @model_validator(mode="after")
    def valid_control(self):
        if self.mode != "none":
            if not self.hedge_weights or any(w < 0 or w > 1 for w in self.hedge_weights.values()):
                raise ValueError("Specify nonnegative hedge weights totaling 100%.")
            if abs(sum(self.hedge_weights.values()) - 1) > 1e-8:
                raise ValueError("Hedge weights must total 100%.")
            if self.mode == "downside_vol" and self.downside_vol is None:
                raise ValueError("Downside settings are required.")
            if self.mode == "trend" and self.trend is None:
                raise ValueError("Trend settings are required.")
        return self
