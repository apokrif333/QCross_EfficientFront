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
  "bootstrap_iterations": 500,
  "cv_folds": 4,
  "expected_block_length": 12,
  "random_seed": 42
}
```

POST this to `/api/v1/analytics/bootstrap` and retrieve the existing job/result
URLs. Multiple objectives may include `gmv`, `max_sharpe` and `target_return` in
one request. A missing target is rejected before job submission. As before,
GMV/Max Sharpe default to a joint run when neither objective field is supplied.

## Result interpretation

Feasibility is counted per generation. The minimum annual expected return must
be feasible on every synthetic Train. Feasibility, numerical failures and unknown
feasibility use the requested generation count as their denominator. A technical
failure marks the result incomplete and suppresses its weight aggregate.
Economic infeasibility is a recorded outcome. Only generations with all folds
successfully fitted contribute to the distributions of the continuous Validation.
The weight statistics describe separate fold fits.

Each fit checks constrained maximum return with tolerance 1e-7. A feasible
target uses the existing minimum-variance solver. An infeasible target leaves
the generation without a complete optimized path.

See [Bidirectional Stationary Bootstrap](bidirectional-stationary-bootstrap.md).
Earlier snapshots retain their historical Original and Resampled portfolios.
The current method reports fold weights and combined Validation metrics.

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

Tests cover binding and nonbinding targets, constrained infeasibility, technical
errors, all four covariance methods, Nominal/Real BIL alignment, API jobs, UI and
snapshot compatibility.
