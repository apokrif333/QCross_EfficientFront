import time
from itertools import combinations, product
from math import comb

import cvxpy as cp
import numpy as np

from app.analytics.constraints import AllocationConstraints
from app.analytics.models import AnalyticsError, OptimizationResult


class PortfolioOptimizer:
    """Reusable parameterized CVXPY QPs; annual means/covariances at the boundary."""

    def __init__(
        self, mean: np.ndarray, covariance: np.ndarray, constraints: AllocationConstraints
    ):
        self.mean, self.covariance, self.constraints = mean, covariance, constraints
        self.weights = cp.Variable(len(mean))
        self.target = cp.Parameter()
        self.aversion = cp.Parameter(nonneg=True)
        self.rules = constraints.cvx(self.weights)
        variance = cp.quad_form(self.weights, cp.psd_wrap(covariance))
        self.gmv_problem = cp.Problem(cp.Minimize(variance), self.rules)
        self.target_problem = cp.Problem(
            cp.Minimize(variance), self.rules + [mean @ self.weights == self.target]
        )
        self.return_problem = cp.Problem(cp.Maximize(mean @ self.weights), self.rules)
        self.utility_problem = cp.Problem(
            cp.Minimize(variance - self.aversion * (mean @ self.weights)), self.rules
        )

    def _solve(
        self, problem: cp.Problem, variable: cp.Variable, *, normalize: bool = False
    ) -> OptimizationResult:
        started = time.perf_counter()
        try:
            problem.solve(
                solver="CLARABEL",
                max_iter=300,
                tol_gap_abs=1e-10,
                tol_feas=1e-10,
                tol_gap_rel=1e-10,
            )
        except cp.SolverError as exc:
            return OptimizationResult(
                "solver_error", None, {"solver": "CLARABEL", "reason": str(exc)}
            )
        stats = problem.solver_stats
        diagnostics = {
            "solver": "CLARABEL",
            "solver_status": problem.status,
            "solve_seconds": stats.solve_time,
            "iterations": stats.num_iters,
            "wall_seconds": time.perf_counter() - started,
        }
        if problem.status != cp.OPTIMAL or variable.value is None:
            return OptimizationResult(str(problem.status), None, diagnostics)
        weights = np.asarray(variable.value).ravel()
        if normalize:
            if weights.sum() <= 0:
                return OptimizationResult("normalization_failed", None, diagnostics)
            weights = weights / weights.sum()
        diagnostics["constraint_violation"] = self.constraints.violation(weights)
        try:
            self.constraints.require_feasible(weights)
        except AnalyticsError as exc:
            return OptimizationResult(
                "constraint_violation", None, dict(diagnostics, reason=str(exc))
            )
        return OptimizationResult("optimal", weights, diagnostics)

    def gmv(self) -> OptimizationResult:
        return self._solve(self.gmv_problem, self.weights)

    def maximum_return(self) -> OptimizationResult:
        return self._solve(self.return_problem, self.weights)

    def target_return(self, target: float) -> OptimizationResult:
        self.target.value = target
        result = self._solve(self.target_problem, self.weights)
        if result.weights is not None and abs(float(self.mean @ result.weights) - target) > 1e-7:
            return OptimizationResult("target_violation", None, result.diagnostics)
        return result

    def utility(self, return_preference: float) -> OptimizationResult:
        # Equivalent to maximizing mu'w - gamma*w'Sigma*w with gamma=1/preference.
        self.aversion.value = return_preference
        return self._solve(self.utility_problem, self.weights)

    def max_sharpe(self, risk_free_rate: float = 0.03) -> OptimizationResult:
        excess = self.mean - risk_free_rate
        attainable = self.maximum_return()
        if attainable.weights is None:
            return attainable
        if excess @ attainable.weights <= 1e-10:
            if abs(float(excess @ attainable.weights)) <= 1e-10:
                attainable.diagnostics["formulation"] = "zero_excess_return_boundary"
                return attainable
            return self._negative_sharpe(excess)
        # Homogeneous QP: y=w/(excess'w), k=1/(excess'w). Retains ALL linear bounds.
        y, scale = cp.Variable(len(excess)), cp.Variable(nonneg=True)
        problem = cp.Problem(
            cp.Minimize(cp.quad_form(y, cp.psd_wrap(self.covariance))),
            self.constraints.cvx(y, scale) + [excess @ y == 1],
        )
        result = self._solve(problem, y, normalize=True)
        result.diagnostics["formulation"] = "homogeneous_convex_qp"
        return result

    def _negative_sharpe(self, excess: np.ndarray) -> OptimizationResult:
        # With excess'w < 0 everywhere, maximizing Sharpe is equivalent to
        # maximizing ||Sigma^(1/2)w|| / (-excess'w). Its maximum on a polytope
        # is attained at a vertex (linear-fractional image followed by a norm).
        # Exhaustively enumerate vertices; never substitute a local optimum.
        rules, n = self.constraints, len(excess)
        candidates = []
        examined = 0
        if not rules.group_names:
            for free in range(n):
                others = [i for i in range(n) if i != free]
                choices = [sorted({rules.lower[i], rules.upper[i]}) for i in others]
                for boundary in product(*choices):
                    w = np.zeros(n)
                    w[others] = boundary
                    w[free] = 1 - w.sum()
                    examined += 1
                    if rules.violation(w) <= 1e-9:
                        candidates.append(w)
        else:
            matrix = np.vstack((np.eye(n), -np.eye(n), rules.group_matrix, -rules.group_matrix))
            bound = np.concatenate(
                (rules.upper, -rules.lower, rules.group_upper, -rules.group_lower)
            )
            count = comb(len(bound), n - 1)
            if count > 300000:
                return OptimizationResult(
                    "computational_limit",
                    None,
                    {
                        "reason": "Nonpositive-excess Max Sharpe exceeds 300,000 vertex bases.",
                        "candidate_bases": count,
                    },
                )
            for active in combinations(range(len(bound)), n - 1):
                system = np.vstack((np.ones(n), matrix[list(active)]))
                examined += 1
                try:
                    w = np.linalg.solve(system, np.r_[1, bound[list(active)]])
                except np.linalg.LinAlgError:
                    continue
                if rules.violation(w) <= 1e-9:
                    candidates.append(w)
        if not candidates:
            return OptimizationResult("vertex_enumeration_failed", None, {})
        weights = max(
            candidates, key=lambda w: float(excess @ w / np.sqrt(w @ self.covariance @ w))
        )
        rules.require_feasible(weights)
        return OptimizationResult(
            "optimal",
            weights,
            {
                "formulation": "exhaustive_vertex_enumeration_nonpositive_excess",
                "examined_bases": examined,
                "constraint_violation": rules.violation(weights),
            },
        )

    def optimize(
        self, objective: str, risk_free_rate: float = 0.03, target_return: float | None = None
    ) -> OptimizationResult:
        if objective == "gmv":
            return self.gmv()
        if objective == "max_sharpe":
            return self.max_sharpe(risk_free_rate)
        if objective == "target_return" and target_return is not None:
            return self.target_return(target_return)
        raise AnalyticsError("Unknown objective or missing target expected return.")
