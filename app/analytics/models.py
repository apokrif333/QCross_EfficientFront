from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

CovarianceMethod = Literal["sample", "ledoit_wolf", "nonlinear", "cv_linear"]
Objective = Literal["gmv", "max_sharpe", "target_return", "max_return_at_user_risk"]
ShrinkageTarget = Literal["identity", "diagonal"]


class AnalyticsError(ValueError):
    """Invalid input or an explicitly failed numerical calculation."""


@dataclass
class ReturnPanel:
    returns: pd.DataFrame
    assets: list[str]
    groups: dict[str, list[str]] = field(default_factory=dict)
    metadata: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_free_returns: np.ndarray | None = None
    reference_metadata: dict = field(default_factory=dict)
    return_basis: str = "nominal"
    risk_free_source: str = "fixed_annual"
    differential: dict = field(default_factory=dict)
    simulation_options: dict = field(default_factory=dict)
    control_config: dict = field(default_factory=dict)
    control_returns: pd.DataFrame | None = None
    control_metadata: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 2 <= len(self.assets) <= 15:
            raise AnalyticsError("Frontier analysis requires 2 to 15 assets.")
        if len(set(self.assets)) != len(self.assets):
            raise AnalyticsError("Duplicate assets are not allowed.")
        if list(self.returns.columns) != self.assets:
            raise AnalyticsError("Return columns must match the explicit asset ordering.")
        values = self.returns.to_numpy(dtype=float)
        if len(values) < 120:
            raise AnalyticsError(
                f"The selected assets have only {len(values)} months of common history. "
                "At least 120 months are required."
            )
        if len(values) > 10000:
            raise AnalyticsError("A maximum of 10,000 monthly observations is supported.")
        if not np.isfinite(values).all() or (values < -1).any():
            raise AnalyticsError("Monthly returns must be finite and at least -100%.")
        if self.risk_free_returns is not None:
            self.risk_free_returns = np.asarray(self.risk_free_returns, dtype=float)
            if (
                self.risk_free_returns.shape != (len(values),)
                or not np.isfinite(self.risk_free_returns).all()
            ):
                raise AnalyticsError("Risk-free observations must match every aligned asset month.")
        periods = pd.PeriodIndex(self.returns.index, freq="M")
        if not periods.equals(pd.period_range(periods[0], periods[-1], freq="M")):
            raise AnalyticsError("The aligned period contains missing or duplicate months.")

    @property
    def values(self) -> np.ndarray:
        return self.returns.to_numpy(dtype=float)


@dataclass
class CovarianceEstimate:
    matrix: np.ndarray  # monthly, including any disclosed numerical regularization
    method: str
    diagnostics: dict[str, Any]
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class OptimizationResult:
    status: str
    weights: np.ndarray | None
    diagnostics: dict[str, Any]

    def require_weights(self) -> np.ndarray:
        if self.weights is None:
            raise AnalyticsError(f"Optimization failed ({self.status}): {self.diagnostics}")
        return self.weights
