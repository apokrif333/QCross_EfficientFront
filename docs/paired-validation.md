# Portfolio out-of-sample robustness

`objective: "max_return_at_user_risk"` selects maximum annual arithmetic expected
return subject to the user's estimated annual training variance. Explicit user
weights must total 100%, be finite and obey the same asset/group constraints.
They are never normalized or changed. CVXPY/Clarabel solves the convex quadratic
risk constraint; allocations and risk are checked with tolerance 1e-7. The
constraint does not guarantee equal realized Validation risk.

Group bounds of 0% and 100% are implied by long-only weights summing to 100%.
These redundant bounds are omitted from solver problems to avoid numerical
instability, while all specified groups remain in feasibility diagnostics.
Positive lower bounds and upper bounds below 100% remain enforced, including
in the homogeneous Max Sharpe formulation.

## Request

```json
{
  "instrument_ids": [1, 175, 243],
  "user_weights": {"1": 0.6, "175": 0.2, "243": 0.2},
  "objective": "max_return_at_user_risk",
  "bootstrap_objectives": ["max_return_at_user_risk"],
  "bootstrap_iterations": 500,
  "expected_block_length": 12,
  "random_seed": 42,
  "return_basis": "real",
  "risk_free_source": "historical_bil",
  "covariance_method": "nonlinear",
  "include_paired_comparison": true
}
```

Submit the same controls to the existing `/api/v1/analytics/frontier`,
`/cross-validation`, `/bootstrap` or `/resampled-frontier` jobs. All four objectives
are supported by the ordinary Frontier/CV/Bootstrap path. Resampled Frontier
continues to use shared risk aversion, independently of the global objective.
The job/status/result API and worker timeout are unchanged.

With user weights, comparison is enabled by default for CV and Bootstrap, even
when another objective is selected. Without weights it is omitted. Set
`include_paired_comparison: false` to replay a legacy request or explicitly omit
comparison; this does not affect existing optimization objectives. Bootstrap
rejects an odd generation count before job submission when pairing is enabled.
Legacy UI snapshots remain readable and are replayed with pairing disabled when
the setting was absent. Selecting the new objective requires feasible weights.

## Bidirectional CV

All existing one-sided blocks, minimum sizes and both training directions remain
unchanged. Every fold fits its selected covariance estimator and mean using
training months only, then chooses maximum return at the fixed user's training
risk. These weights are frozen and evaluated alongside the user on exactly the
same Validation months. CV-linear hyperparameter selection is confined to Train.
The original requested CV objective and ensemble still run alongside comparison.

## Paired Bootstrap

The existing Stationary Bootstrap RNG generates the same index stream as before.
Odd generations train; the immediately following even generations validate.
No extra draws are made. Each generation has the full selected-history length.
All assets and historical BIL use the same indices. Nominal/Real transformation
is performed once on the aligned panel before sampling. Covariance is reused
from the existing Bootstrap iteration, with conditional cv_linear parameters
held fixed as previously implemented. Every generation still contributes to
the user's path distributions, including terminal wealth from $1.

Training/Validation draws are independent draws from the same empirical history.
This is conditional synthetic validation, not an independent future-market test.
Optional comparison failures have separate counts and do not alter the status or
failure/feasibility policy of GMV, Max Sharpe or Target Return Bootstrap.

## Results

`paired_comparison` contains `requested_comparisons`, `valid_count`, `failed_count`,
`results`, `differences`, and `win_frequencies`. Bootstrap additionally reports
`generated_datasets`, `horizon_months`, `user_weights`, `clouds`, `annual_mean`,
`annual_covariance`, `validation_frontier` and an analysis version. A 500-generation
run yields 250 comparisons, 250 asset/user cloud observations and up to 250
optimized cloud observations, depending on train-solver success. The existing
`efficiency_robustness.distributions` separately retains 500 user path values
per defined metric. Undefined Sharpe values are null with explicit valid counts.

Both portfolios' Validation metrics and optimized weights are recorded per pair
or fold. Differences are optimized minus user: annual arithmetic return, CAGR,
realized volatility, historical BIL differential Sharpe, and maximum drawdown.
Drawdown is a positive loss magnitude, so negative drawdown/volatility deltas
represent improvement. New realized-volatility comparisons use ddof=1, while
legacy fixed-path distributions retain their established ddof=0 convention.
All paths use monthly fixed-weight rebalancing, including Real returns.

Each difference has mean, median, P5/P95 and min/max, plus a metric-specific valid
count. Win frequencies disclose their own denominators: higher CAGR, higher
Sharpe, lower volatility, and higher CAGR with no higher realized volatility.
Higher return with higher risk is not labeled dominance. Percentiles and win
frequencies describe these scenarios and do not predict future probabilities.

## Visualization

The Validation view uses normalized Plotly 2D histogram contours, with stronger
color for denser regions. Portfolio clouds are visible by default; each asset
cloud can be toggled through the legend. X is realized annual volatility and Y
annual arithmetic sample return, displayed as percentages. Hover labels identify
the distribution and density. The clouds contain individual Validation outcomes,
not portfolios evaluated at average weights.

`Ex-post Validation Efficient Frontier` uses the arithmetic average of the
even-generation annual empirical means and covariances (ddof=1). It is a
retrospective constrained reference; it is separate from the unchanged shared
risk-aversion Resampled Frontier. No averaged-weight reference marker is added.

## Reproducibility and verification

Engine 2.3.1 includes the paired module in the source digest. User allocation,
pairing option, return basis, data/reference fingerprints, covariance settings,
seed and unchanged sampling fingerprint identify the calculation.

Deterministic tests cover all four covariance methods and both bases, independent
shared index draws, BIL alignment, exact moments/deltas, CV isolation, all fold
directions, feasibility/errors, legacy objective preservation and async jobs.
`test_500_datasets_have_250_pairs_and_500_user_paths` measured 4.777 seconds for
500 generations / 250 comparisons on the seeded 240-month, three-asset fixture
in this workspace. This is a synthetic local benchmark, not a production timing
guarantee; the configured default worker timeout is 300 seconds.
