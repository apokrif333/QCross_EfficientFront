# Portfolio analytics

The analytics engine takes aligned monthly simple total returns. Only `data_loader.py`
imports SQLAlchemy; numerical modules have no FastAPI/database dependency. No historical
observations, ingestion adapters or validated series metadata are changed.

## Data and mathematical conventions

Use exactly one validated USD monthly nominal total-return series per instrument. No
currency conversion occurs. Period endpoints are the intersection of stored histories
and the requested month-inclusive dates. The response discloses the resulting interval.
An internal missing month is an error, never a zero or a filled observation. Require
2–15 distinct assets, 120 common months, and at most 10,000 monthly observations.

Expected annual return is `12 * mean(monthly_return)`. Annual covariance is
`12 * monthly_covariance`; annual volatility is the square root of portfolio annual
variance. The annual risk-free rate defaults to 0.03 and is subtracted from expected
annual arithmetic return to calculate Sharpe. Historical returns assume monthly
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
  minimizing per-observation Gaussian validation NLL. Consecutive unshuffled
  K-folds; validation residuals use the training mean. NLL totals are weighted
  by fold size. Ties select the first/smallest alpha. Return grid, NLL scores,
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

## Classical cross-validation

Split T observations into K approximately equal consecutive folds, without shuffle.
For each validation fold, train on the other K−1 folds, including observations
before and after validation. This is classical K-fold, not a time-ordered forecast
experiment. K is 2–10 (default 5). Reject `T-ceil(T/K)<120` before fitting.
Training periods disclose their disjoint consecutive segments.

Select GMV, Max Sharpe or an annual target return before CV. Keep that objective
and all constraints fixed across folds. Each fold estimates its means and
covariance using training observations only. For `cv_linear`, an inner classical
K-fold selects alpha using only the outer training subset. Inner covariance
fits do not optimize portfolios and therefore do not require 120 months each.
No outer validation data tunes its evaluated portfolio.

Apply each fold's fixed optimized weights to validation months. Return annualized
realized arithmetic return, sample volatility, Sharpe (if defined), total return,
CAGR and drawdown. These statistics are specific to that validation fold.

Average fold weights in identical asset ordering only when all folds succeed.
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
successful iterations for each objective. Below that threshold return `incomplete`
and withhold a misleading accepted resampled portfolio. Successful weight vectors
are averaged and checked against all constraints. Original and resampled portfolios
are both evaluated on the original full-sample estimates and history.

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
