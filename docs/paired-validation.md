# Portfolio validation robustness

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
  "cv_folds": 4,
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
`include_paired_comparison: false` to omit comparison. Both even and odd
generation counts are accepted. Legacy UI snapshots remain readable and replay
uses the current methodology. Selecting the user-risk objective requires
feasible weights.

## Bidirectional CV

All existing one-sided blocks, minimum sizes and both training directions remain
unchanged. Every fold fits its selected covariance estimator and mean using
training months only, then chooses maximum return at the fixed user's training
risk. These weights are frozen and evaluated alongside the user on exactly the
same Validation months. CV-linear hyperparameter selection is confined to Train.
The original requested CV objective and ensemble still run alongside comparison.

## Paired Bootstrap

Each generation independently resamples Train and Validation within every
bidirectional Fold, using disjoint source months within that Fold. All assets,
historical BIL and hedges share the sampled indices. The aligned Nominal/Real
transformation comes before sampling. Every estimator, including CV Linear,
is fitted on the synthetic Fold Train. With controls enabled, the risk budget
comes from net realized annual volatility on the same synthetic Train.

The Validation blocks are concatenated in source order. The optimizer uses
the corresponding fitted weights within each block and trades to new targets
at the boundary. The user's active and passive paths use their regular
rebalancing rules on the same full Validation. A missing optimizer Fold fit
excludes the whole generation from the corresponding optimized comparison.
See [the current methodology](bidirectional-stationary-bootstrap.md).

## Results

`paired_comparison` contains `requested_comparisons`, `valid_count`, `failed_count`,
`results`, `differences`, and `win_frequencies`. Bootstrap additionally reports
`generated_datasets`, `horizon_months`, `user_weights`, `clouds`, `annual_mean`,
`annual_covariance`, `validation_frontier` and an analysis version. A 500-generation
run requests 500 full Validation comparisons. The optimized cloud and the paired
differences include only generations with all required Fold fits. Undefined
metrics are null with explicit valid counts.

The Bootstrap result records full-path metrics and each Fold's fitted target
weights. Plots report optimized minus user. The passive view reports passive
minus active on the same scenarios. Drawdown is a positive loss magnitude,
so negative drawdown and volatility deltas favor the first portfolio.

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
joined Validation annual empirical means and covariances (ddof=1). It is a
retrospective constrained reference; it is separate from the unchanged shared
risk-aversion Resampled Frontier. No averaged-weight reference marker is added.

## Reproducibility and verification

The engine records the methodology, Fold geometry and sample fingerprints.
Deterministic tests cover source-month isolation, shared index draws, BIL
alignment, continuous simulation, exact metrics, async jobs and legacy
snapshots. The [methodology page](bidirectional-stationary-bootstrap.md)
reports the comparison benchmark.
