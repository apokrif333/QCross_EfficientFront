# Portfolio analytics

For the return/CAGR reconciliation, numbered user groups, user reference portfolio
and UI conventions, see [return-conventions.md](return-conventions.md).

The analytics engine takes aligned monthly simple total returns. Only `data_loader.py`
imports SQLAlchemy; numerical modules have no FastAPI/database dependency. No historical
nominal observations or validated nominal series metadata are changed. Versioned
macro reference storage and ingestion are documented in [macro-analytics.md](macro-analytics.md).

## Data and mathematical conventions

Use exactly one validated USD monthly nominal total-return series per instrument. No
currency conversion occurs. Period endpoints are the intersection of stored histories
and the requested month-inclusive dates. The response discloses the resulting interval.
An internal missing month is an error, never a zero or a filled observation. Require
2–15 distinct assets, 120 common months, and at most 10,000 monthly observations.

Expected annual return is `12 * mean(monthly_return)`. Annual covariance is
`12 * monthly_covariance`; annual volatility is the square root of portfolio annual
variance. New requests default to monthly historical BIL. Estimated Sharpe uses
the mean and estimated covariance of asset-minus-BIL monthly differences;
historical Sharpe uses their observed sample standard deviation. An explicit
legacy fixed annual mode retains its original model-based objective. Historical returns assume monthly
rebalancing: `r_p,t = returns[t] @ weights`. CAGR is compounded separately, using
`exp(12 * mean(log(1 + r_p,t))) - 1`. Maximum drawdown includes initial wealth before
the first observation. Drawdown is reported as a positive loss fraction. Undefined
correlations and Sharpe ratios are JSON null, not NaN or infinity.

## Four covariance estimators

* `sample`: centered sample covariance with `ddof=1`.
* `ledoit_wolf`: scikit-learn's classical `LedoitWolf`, scaled-identity target.
  This implementation uses its native maximum-likelihood normalization (`ddof=0`).
  The selected intensity is returned.
* `nonlinear`: analytical nonlinear shrinkage of Ledoit and Wolf (2020),
  [DOI](https://doi.org/10.1214/19-AOS1921). Demean, use effective `n=T-1`,
  Epanechnikov density and Hilbert-transform kernels, bandwidth `n^(-1/3)`, then
  transform each eigenvalue using equations 4.3 and 4.7–4.9. The `p<=n` branch
  covers all supported calculations. Reject rank-deficient or near-singular
  estimator inputs explicitly. Reviewed against the authors'
  [MATLAB archive](https://www.econ.uzh.ch/dam/jcr:11d24ab0-7ec2-4b3f-8ef4-7affaa727d25/analytical_shrinkage.m.zip)
  and tested numerically against pinned `nonlinshrink==0.7`, the independent
  [Python implementation](https://github.com/matzhaugen/analytic_shrinkage).
* `cv_linear`: `(1-alpha)*S + alpha*target` with `S` using `ddof=1`. Target is
  `identity` (trace-scaled identity) or `diagonal` (preserves individual sample
  variances). Choose alpha on a deterministic 101-value grid from 0 through 1,
  minimizing per-observation Gaussian validation NLL. Bidirectional one-sided
  block folds use at least 60 training and 30 validation months per fold;
  validation residuals use the training mean. NLL totals are weighted
  by validation size and divided by the total validated observations (including
  incomplete K=2 coverage). Ties select the first/smallest alpha. Return grid, NLL scores,
  target and intensity. No Sharpe-based estimator selection is used.

Every estimate is checked for finiteness, symmetry and eigenvalues. Material
asymmetry or negative eigenvalues cause an error. Symmetry is averaged within
roundoff tolerance. Eigenvalues below `max(largest_eigenvalue*1e-10, 1e-14)` are
floored, with eigenvalues, condition numbers and Frobenius change returned. This
is numerical regularization within the selected estimator, not a fallback to
another estimator. It can introduce a tiny positive modeled variance into a
zero-variance direction. Such results must be read with their diagnostics.

## Constraints and deterministic portfolios

CVXPY/CLARABEL calculates constraint feasibility, GMV, maximum attainable return,
target-return minimum variance and the 51-point default frontier from GMV return
to maximum return. Total weight is one, with individual bounds in [0,1]. Explicit
category/subcategory and curated `analytics_groups` mappings support overlapping
groups. `CATEGORY_GROUPS` documents the broad source-category mapping. Unknown
groups or unselected assets are rejected, and infeasible bounds are never relaxed.

Target return is a **minimum required annual arithmetic expected return**:
`min w' covariance w` subject to `mean'w >= target` and the allocation constraints.
It is expressed in the selected Nominal/Real basis, not CAGR. GMV is optimal if it
already meets the target. This same optimizer is used by Frontier, CV and Bootstrap.

For positive attainable excess return, Max Sharpe uses the homogeneous convex
QP `min y' Sigma y` with `excess'y=1`, `sum(y)=k`, and every original allocation
bound multiplied by k. Recover `w=y/sum(y)` and verify the original constraints.
When all feasible excess returns are negative, the maximum is attained at a
polytope vertex. Exhaustive vertex enumeration is used (bounded at 300,000
active bases for grouped constraints); exceeding this work limit returns an
explicit failure. A feasible zero-excess boundary has Sharpe zero.
Every solver result includes status, solver, iterations, timing and constraint
violation. `optimal_inaccurate` is not silently accepted.

The equal-weight benchmark uses the same estimates and monthly-rebalanced
history. If it violates constraints, its label is `Unconstrained equal-weight
reference`, with `feasible=false`.

Correlation warnings trigger above absolute 0.98. Shared historical sources are
reported only when explicit metadata establishes them. Reconstruction warnings
are separate; detailed proxy transitions may be unknown. Unequal endpoints,
stale endpoints (>2 months), and common history shorter than 180 months are
also disclosed.

## Bidirectional one-sided block cross-validation

Method identifier: `bidirectional_one_sided_block_kfold`. K is even, 2 through 20,
default 4. Even K gives paired validation blocks on the two halves of the sample
without an odd central block; it does not guarantee equal direction counts when
small samples and configurable limits create a tie.
For K >= 4, use `np.array_split(np.arange(T), K)` equivalently: first `T % K`
blocks get one extra month. Every observation is validated once. Train on the
longer interval strictly before or strictly after the validation block; ties
select the earlier interval. Never concatenate the two sides. Both periods are
contiguous, increasing, deterministic, and disjoint within each fold. There is
no embargo or purge gap. Training samples across separate folds may overlap:
this deliberately measures sensitivity to different historical samples/regimes.

Portfolio CV requires **120 train / 60 validation months per fold**. Covariance
CV requires **60 / 30**. Ordinary frontier optimization still requires only
120 common months globally; a sample/Ledoit-Wolf/nonlinear frontier does not
require portfolio CV feasibility.

For K=2, use a complete two-block chronological partition whenever both opposite
training blocks meet the training minimum (including odd T). Otherwise use equal
outer validation windows of size `min(floor(T/2), T-min_train)` with the complete
opposite interval as training. Reject if either window is smaller than
`min_validation`. Windows cannot overlap; central months may be unvalidated.
At T=180 with portfolio 120/60, validate 0–59 against train 60–179, then validate
120–179 against train 0–119 (zero-based inclusive). Coverage is 120/180; training
overlaps over 60–119. At T=200, outer validation windows contain 80 months; at
T=240, both contain 120. T=179 is invalid. Training overlaps are intentional.

| K | Portfolio minimum T | Covariance minimum T |
|---|---:|---:|
| 2 | 180 | 90 |
| 4 | 240 | 120 |
| 6 | 360 | 180 |
| 8 | 480 | 240 |
| 10 | 600 | 300 |
| 12 | 720 | 360 |
| 14 | 840 | 420 |
| 16 | 960 | 480 |
| 18 | 1080 | 540 |
| 20 | 1200 | 600 |

At T=240, K=4, validation ranges are 0–59 / 60–119 / 120–179 / 180–239;
training ranges are 60–239 / 120–239 / 0–119 / 0–179. At T=10,000, K=4,
validation has 2,500 rows each and training has 7,500 / 5,000 / 5,000 / 7,500.

Select GMV, Max Sharpe or an annual target return before CV. Keep that objective
and all constraints fixed across folds. Each fold estimates its means and
covariance using training observations only. For `cv_linear`, the same shared
splitter performs inner CV exclusively on the extracted outer training subset,
with the same K and the covariance 60/30 minimums. Select alpha by validation
Gaussian NLL, then refit covariance on all outer training observations. Means and
optimized weights also use only this subset. No outer validation data influences
shrinkage selection, covariance, means, or weights. The separate full-sample
original solution may estimate covariance on the full selected history.

Apply each fold's fixed optimized weights to validation months. Return annualized
realized arithmetic return, sample volatility, Sharpe (if defined), total return,
CAGR and drawdown. These statistics are sample-sensitivity and allocation-stability
diagnostics. Because training may occur later than validation, they are not a
historical investable backtest or independent forward out-of-sample performance.
Each fold reports direction, single-segment start/end/count, weights, training
and validation metrics, covariance parameters/diagnostics and solver diagnostics.
The overall result reports total and validated observations, coverage fraction,
unvalidated indices, selected K and both sets of minimums.

Average fold weights equally in identical asset ordering only when all folds succeed,
regardless of training length: `w_ensemble = sum(w_fold)/K`.
Linear constraints survive averaging, and the averaged vector is checked again.
Evaluate original and ensemble weights on the same full-sample mean, covariance
and returns. Ensemble statistics are descriptive full-sample characteristics,
**not independent out-of-sample performance**. A failed fold yields an incomplete
CV result with no partial ensemble substituted.

Weight dispersion uses population standard deviation across the observed folds.
Mean absolute weight deviation averages `abs(w_fold - mean_fold_weight)` across
assets and folds; maximum deviation is the largest such value. Allocation turnover
relative to the original is `0.5*sum(abs(w_fold - w_original))`, averaged across
folds. Constraint-hit frequencies use absolute weight tolerance `1e-6`.

## Stationary bootstrap and resampling

Only [Politis and Romano's (1994) Stationary Bootstrap](https://doi.org/10.1080/01621459.1994.10476870)
is implemented. Draw uniform starting indices and geometrically distributed block
lengths with restart probability `p=1/L`. Circularly wrap at the last observation,
concatenate blocks and truncate to exactly T rows. Resample whole monthly vectors
jointly, never columns separately. L is 3–24 months (default 12); seed defaults to
42. Given the same T, L, seed and iteration count, every estimator receives the
same observations; a SHA-256 fingerprint of the sample indices confirms this.

The default portfolio bootstrap runs 1,000 iterations for both GMV and Max Sharpe.
Re-estimate means and covariance and optimize under the original constraints on
every sample. `cv_linear` holds the original target and selected intensity fixed:
this is explicitly a **conditional bootstrap**, excluding hyperparameter-selection
uncertainty. Other estimators are fitted afresh, including Ledoit-Wolf intensity.

Keep all iteration records, including failed estimator/solver statuses, reasons,
weights, numerical diagnostics and sample statistics. Require at least 95%
successful iterations for GMV and Max Sharpe. Below that threshold return `incomplete`
and withhold a misleading accepted resampled portfolio. Successful weight vectors
are averaged and checked against all constraints. Original and resampled portfolios
are both evaluated on the original full-sample estimates and history.

Target Return Bootstrap checks the constrained maximum achievable expected return
for every sample (annual-return tolerance `1e-7`). `target_infeasible` is an economic
outcome and does not count as a technical failure or activate the 95% gate.
`target_feasibility_rate` counts certified feasible samples divided by **all requested**
samples; a later target-solver failure can still have certified feasibility. Failed
feasibility/estimation leaves feasibility unknown and is counted separately.
For Target Return, any technical sample failure or failure to compute the original
solution suppresses the aggregate (`incomplete`); no partial aggregate is accepted.
When computation succeeds, average weights and intervals use only successful,
feasible samples. Zero feasible samples is a completed result with null resampled
portfolio and stability. An infeasible original target has no original portfolio;
feasible resamples may still have conditional statistics, with null original weights
and allocation distance. Evaluate the average on original full-sample estimates and
report its actual return, target shortfall/surplus and whether it meets the target.
Do not project the average onto the target constraint. Feasibility across historical
resamples is not the probability of future target returns.

Asset statistics include original, mean, median, population standard deviation,
P5/P95, minimum/maximum and constraint-hit frequencies (tolerance `1e-6`). Allocation
distance is `0.5*sum(abs(w_bootstrap-w_original))`; report mean, median, P5/P95.
These measure sensitivity to historical sampling and are **not guaranteed future
return intervals**. Resampling does not imply superior future performance.

Optional resampled frontier defaults to 250 samples and 51 shared risk-aversion
parameters, logarithmically spaced from 10,000 to 0.001. For each fixed gamma solve
`max mean'w - gamma*w'covariance*w` on every sample and average **weights at that
same gamma**. Evaluate those weights using original full-sample estimates. Never
average unrelated sample frontier points by index. Custom positive finite gamma
values up to 1e6 are accepted. A sample succeeds only if every gamma succeeds;
the same 95% sample threshold applies. Return this curve separately and label it
as a resampled approximation, not the exact classical efficient frontier.

When user weights are supplied, the resampled-frontier result also reports the
fixed portfolio on the original sample and its average annual expected return
and estimated volatility across the same successful bootstrap samples. The
curve uses averaged weights evaluated on the original sample; the sample-average
point uses per-sample moments and is identified separately on the chart.

Resampled Frontier is independent of the global portfolio objective and target
return. It accepts GMV, Max Sharpe and Target Return selections, including a null
or unattainable target, without altering the shared risk-aversion curve. Target
Return Bootstrap remains a separate conditional allocation analysis. Explicit
Bootstrap objective lists are sent unchanged by the frontend; defaults still
select both GMV and Max Sharpe.


## Historical BIL and inflation

New Live requests default to historical monthly BIL and nominal USD. Real mode
deflates the complete asset/BIL panel before estimation, optimization, CV and
Stationary Bootstrap. Estimated and historical Sharpe are reported separately.
Legacy fixed-rate snapshots retain their original semantics.

See [source, formula, refresh commands, API, revisions and verification](macro-analytics.md).

# Portfolio efficiency robustness

This section describes legacy diagnostics. The primary portfolio comparison now
uses [training-only selection and paired Validation](paired-validation.md).
The rolling endpoint and snapshot schema are retained for compatibility, and
its launch controls have been removed. Ex-post distances are collapsed under
additional retrospective diagnostics.

For an explicitly supplied, feasible user allocation, analytics can calculate two
exact constrained distances to each sample's own mean/variance frontier. Return
shortfall holds annual volatility at the user's level; excess volatility holds
annual arithmetic return at the user's level. Each is solved as a convex
optimization with Clarabel and the selected asset/group constraints. No plotted
frontier grid is used, and fixed user weights are never changed.

Rolling robustness uses complete 120-month windows at 12-month steps. CV results
use each validation block's ex-post estimated frontier; these are retrospective
benchmarks, while trained CV allocations and metrics remain unchanged. For
`cv_linear`, validation covariance uses the shrinkage parameters selected on its
training subset. Bootstrap robustness reuses the exact existing stationary
bootstrap samples and covariance estimates. Its percentiles describe historical
resampled scenarios, not confidence intervals or future-return probabilities.
An allocation that violates the selected constraints is marked
`not_comparable`; numerical failures remain explicit and do not alter the main
CV or Bootstrap computation status.

See [verification commands, regression coverage and measured performance](efficiency-robustness-verification.md).

## Fixed-portfolio Bootstrap distributions

Each existing stationary sample also evaluates the unchanged user allocation's
CAGR, annual arithmetic return, historical annual volatility, maximum drawdown,
historical differential-return Sharpe and terminal wealth from $1. Annual
volatility uses the simulated path's monthly sample standard
deviation with ddof=1, multiplied by sqrt(12); Sharpe uses ddof=1 on the monthly
portfolio-minus-aligned-risk-free series. The estimated volatility used for the
efficiency distances remains separately available as `user_volatility`.

The asset and BIL arrays use the same existing sampled indices, after the global
Nominal/Real transformation. No additional samples are generated. Every sample
has the full T months of the selected common return panel: a 240-month history
produces 240-month synthetic scenarios, not 120-month scenarios. Rolling
Efficiency continues to use separate 120-month windows with 12-month steps.

All eight metrics expose mean, median, P5, P95, minimum and maximum, along with
their scenario horizon. CAGR, arithmetic return, volatility, Sharpe and the two
efficiency distances are annualized. Drawdown and terminal wealth cover the
entire scenario horizon. Real terminal wealth is in units of initial purchasing
power from one initial USD. Drawdown includes initial wealth in the running peak.

Path metrics use all samples in which that metric is defined, independently of
frontier-solver success. Distances require successful comparable frontier solves.
Each distribution discloses its available/unavailable counts; zero differential
volatility gives a null Sharpe, and unrepresentable wealth/CAGR values are null.
Percentiles describe historical resampled scenarios and are not calibrated
probabilities of future returns.

## Rebalancing simulation (engine 3.0)

New calculations simulate historical holdings with annual rebalancing and optional
self-financing commissions, including initial purchases. Model-based arithmetic
means/covariances remain unchanged. Historical reporting is net of costs with
gross results disclosed separately. See [portfolio simulation](rebalancing-engine.md) for conventions and legacy snapshot replay.

The standalone [Rebalancing Optimization](rebalancing-optimization.md) job compares
all 21 calendar/relative-threshold rules with identical inputs and costs.
It does not change optimization moments, CV splits or Bootstrap sampling.
