from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DownsideSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    target_annual: float = Field(gt=0, le=10)
    lookback_months: int = Field(strict=True, ge=3, le=24)


class PortfolioControls(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["none", "downside_vol"] = "none"
    hedge_weights: dict[str, float] = Field(default_factory=dict, max_length=15)
    downside_vol: DownsideSettings | None = None

    @model_validator(mode="after")
    def valid_control(self):
        if self.mode != "none":
            if not self.hedge_weights or any(w < 0 or w > 1 for w in self.hedge_weights.values()):
                raise ValueError("Specify nonnegative hedge weights totaling 100%.")
            if abs(sum(self.hedge_weights.values()) - 1) > 1e-8:
                raise ValueError("Hedge weights must total 100%.")
            if self.downside_vol is None:
                raise ValueError("Downside settings are required.")
        return self
