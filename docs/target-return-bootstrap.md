# Target Return Stationary Bootstrap

Engine 2.1.0 treats target return as a minimum annual arithmetic expected return:
minimize variance subject to `mean @ weights >= target_return`, retaining all asset
and group bounds. Frontier, one-sided CV and Stationary Bootstrap share this solver.
If GMV already meets the target, it remains an optimum. The target uses the selected
Nominal/Real basis. Stored results from earlier engines retain their recorded engine
version and values; they are not recalculated or relabeled with the new semantics.

## Request

```json
{
  "instrument_ids": [1, 175, 243],
  "currency": "USD",
  "return_basis": "real",
  "risk_free_source": "historical_bil",
  "covariance_method": "nonlinear",
  "objective": "target_return",
  "target_return": 0.08,
  "bootstrap_objectives": ["target_return"],
  "bootstrap_iterations": 1000,
  "expected_block_length": 12,
  "random_seed": 42
}
```

POST this to `/api/v1/analytics/bootstrap` and retrieve the existing job/result
URLs. Multiple objectives may include `gmv`, `max_sharpe` and `target_return` in
one request. A missing target is rejected before job submission. As before,
GMV/Max Sharpe default to a joint run when neither objective field is supplied.

## Result interpretation

Under `objectives.target_return`, results include:

- `target_return`, `target_feasibility_rate`, `target_feasible_iterations`,
  `target_infeasible_iterations`, `numerical_failure_iterations` and
  `feasibility_unknown_iterations`.
- `computational_status`, separate from feasibility. A low feasible fraction
  does not invalidate the result. The denominator is all requested iterations.
- `original_target_feasible` and `original`. An infeasible original is explicitly
  `status=target_infeasible`, `weights=null`, `metrics=null`.
- `resampled` and `stability`, conditional on successful feasible samples. Weight
  statistics retain mean/median/P5/P95/SD, extrema, bound-hit frequencies and distance
  from original. Without an original portfolio, original weights and distance are null.
- `resampled_target_evaluation`: actual `expected_return`, `target_shortfall`,
  `target_surplus`, `meets_target`, `evaluation=original_full_sample_estimates` and
  `target_guaranteed=false`. The average may fail the target on original estimates.

Every sample first solves constrained maximum return to certify feasibility,
using annual-return tolerance `1e-7`. A boundary discrepancy within this tolerance
may use that certified maximum as the effective target, disclosed in diagnostics.
Successful feasible samples then solve the minimum-variance target problem.
`target_infeasible` never causes substitution of GMV, maximum-return or another
portfolio. It is not included in technical `failed_iterations` or `failures`;
all outcomes remain in `iterations`. A target-solver error after certification
counts as feasible for the feasibility rate, but its weights are never used.
Estimation/feasibility-solver failures leave feasibility unknown.

**Failure policy:** GMV/Max Sharpe retain their existing 95% successful-sample gate.
Target Return accepts economic infeasibility at any frequency, but any technical
sample failure (or technical failure on the original solution) produces `incomplete`
and withholds all aggregates. With zero feasible samples and no technical failures,
return `complete` with `resampled=null` and `stability=null`.

The feasibility rate measures reachability across historical resamples under the
constraints. It is not a forecast probability of earning the target. Conditional
weight intervals measure allocation sensitivity and are not future return intervals.

Stationary sampling, shared asset/BIL indices, the Nominal/Real transformation,
CV splits, covariance formulas and all allocation constraints are unchanged.
`cv_linear` continues to hold original selected asset/differential shrinkage
parameters fixed within Bootstrap. Other covariance methods refit each sample.
Defaults remain 1,000 samples, 12-month expected blocks and seed 42. Engine/source
fingerprints record the changed target semantics; historical snapshots remain readable.

## Resampled Frontier independence (engine 2.1.1)

Resampled Frontier uses its existing shared risk-aversion weight averaging for
every global `objective` selection. It ignores `objective` and `target_return`
when constructing the curve, including a null or infeasible target. API and
frontend validation do not require a target for this operation; requests and
snapshots still retain the global settings as provenance. The covariance method,
asset/group constraints, return basis, reference panel, sampling indices and
risk-aversion parameters continue to control the curve. The numerical algorithm
is unchanged. Bootstrap continues to honor explicit `bootstrap_objectives`,
including Target Return alone or mixed with GMV/Max Sharpe.

## Verification

Deterministic tests cover binding/nonbinding targets, constrained infeasibility,
conditional averages/quantiles, original-target infeasibility, zero feasible samples,
technical errors, original-sample target shortfalls, all four covariance methods,
Nominal/Real BIL alignment, CV/Frontier regression, API jobs, UI and snapshots.
See [verification commands and results](analytics-verification.md).
