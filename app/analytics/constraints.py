from dataclasses import dataclass

import cvxpy as cp
import numpy as np

from app.analytics.models import AnalyticsError


@dataclass
class AllocationConstraints:
    assets: list[str]
    lower: np.ndarray
    upper: np.ndarray
    group_matrix: np.ndarray
    group_lower: np.ndarray
    group_upper: np.ndarray
    group_names: list[str]

    def cvx(self, weights: cp.Expression, scale: cp.Expression | float = 1) -> list:
        rules = [
            cp.sum(weights) == scale,
            weights >= self.lower * scale,
            weights <= self.upper * scale,
        ]
        if len(self.group_names):
            rules.extend(
                [
                    self.group_matrix @ weights >= self.group_lower * scale,
                    self.group_matrix @ weights <= self.group_upper * scale,
                ]
            )
        return rules

    def violation(self, weights: np.ndarray) -> float:
        values = [
            abs(float(weights.sum()) - 1),
            float(np.max(self.lower - weights)),
            float(np.max(weights - self.upper)),
        ]
        if len(self.group_names):
            allocation = self.group_matrix @ weights
            values += [
                float(np.max(self.group_lower - allocation)),
                float(np.max(allocation - self.group_upper)),
            ]
        return max(0.0, *values)

    def require_feasible(self, weights: np.ndarray, tolerance: float = 1e-7) -> None:
        if not np.isfinite(weights).all() or self.violation(weights) > tolerance:
            raise AnalyticsError("Returned weights violate the original allocation constraints.")


def build_constraints(
    assets: list[str],
    asset_bounds: dict | None = None,
    group_bounds: dict | None = None,
    memberships: dict[str, list[str]] | None = None,
) -> AllocationConstraints:
    asset_bounds, group_bounds, memberships = (
        asset_bounds or {},
        group_bounds or {},
        memberships or {},
    )
    unknown = set(asset_bounds) - set(assets)
    if unknown:
        raise AnalyticsError(f"Asset constraints reference unselected assets: {sorted(unknown)}.")

    def bounds(value: dict) -> tuple[float, float]:
        low, high = value.get("min", 0.0), value.get("max", 1.0)
        if not np.isfinite([low, high]).all() or not 0 <= low <= high <= 1:
            raise AnalyticsError("Every allocation bound must satisfy 0 <= min <= max <= 1.")
        return float(low), float(high)

    individual = [bounds(asset_bounds.get(a, {})) for a in assets]
    grouped, limits = [], []
    for group, bound in group_bounds.items():
        members = memberships.get(group)
        if not members or not set(members) <= set(assets):
            raise AnalyticsError(f"Group {group!r} has no valid explicit selected-asset mapping.")
        grouped.append([float(a in members) for a in assets])
        limits.append(bounds(bound))
    result = AllocationConstraints(
        assets,
        np.array([b[0] for b in individual]),
        np.array([b[1] for b in individual]),
        np.array(grouped).reshape(len(grouped), len(assets)),
        np.array([b[0] for b in limits]),
        np.array([b[1] for b in limits]),
        list(group_bounds),
    )
    weights = cp.Variable(len(assets))
    problem = cp.Problem(cp.Minimize(0), result.cvx(weights))
    try:
        problem.solve(solver="CLARABEL", max_iter=200)
    except cp.SolverError as exc:
        raise AnalyticsError(f"Constraint feasibility solver failed: {exc}") from exc
    if problem.status != cp.OPTIMAL or weights.value is None:
        raise AnalyticsError(f"Allocation constraints are infeasible ({problem.status}).")
    result.require_feasible(weights.value)
    return result
