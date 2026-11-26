# Analytics API and integration

## Project structure

```text
app/
  analytics/
    __init__.py
    models.py              # plain dataclasses, errors and explicit asset ordering
    data_loader.py         # read-only database queries, alignment and source mappings
    estimators.py          # all four monthly covariance estimators and diagnostics
    constraints.py         # individual/overlapping group constraints and feasibility
    metrics.py             # arithmetic expectations, monthly-rebalanced CAGR/drawdown
    optimizer.py           # CVXPY GMV, targets, maximum return and Max Sharpe
    frontier.py            # deterministic frontier and equal-weight benchmark
    cross_validation.py    # one-sided block portfolio CV and equal-weight CV ensemble
    splitting.py           # shared portfolio/covariance CV splitting and feasibility
    bootstrap.py           # stationary bootstrap and resampled frontier
    jobs.py                # bounded spawned processes, deadlines and job retention
  api/routes/frontier.py   # analytics submission, status and result endpoints
  schemas/frontier.py      # shared Pydantic request and typed result schemas
tests/analytics/
  conftest.py              # explicitly synthetic deterministic fixture
  test_deterministic.py
  test_cross_validation.py
  test_bootstrap.py
  test_api.py              # real process integration, health/capacity/timeout tests
scripts/analytics_demo.py  # actual stored USD histories; no ingestion/fallback
docs/
  analytics.md             # mathematical and methodological conventions
  analytics-api.md
  analytics-demo/          # observed results, charts, report and OpenAPI schemas
```

## Endpoints

All four submission endpoints return HTTP 202 with a job descriptor:

```text
POST /api/v1/analytics/frontier
POST /api/v1/analytics/cross-validation
POST /api/v1/analytics/bootstrap
POST /api/v1/analytics/resampled-frontier
GET  /api/v1/analytics/jobs/{job_id}
GET  /api/v1/analytics/jobs/{job_id}/result
```

The [Pydantic schemas](../app/schemas/frontier.py) are the implementation source.
The generated [OpenAPI snapshot](analytics-demo/openapi.json) provides complete
machine-readable request and response schemas, including job/result models.
The application's `/openapi.json` always reflects the running source.

Instrument ID strings are allocation-map keys and weight keys. Ticker/source
metadata is returned separately; input order is preserved in all arrays/vectors.
Allocation fractions and return/volatility fractions use decimals, not percentages.

## Shared request

`capital_gains_tax_rate` is a decimal fraction from 0 to 1 and defaults to 0.
The tax model nets nominal realized profits and losses for each historical or
synthetic calendar year. Buy fees enter average cost and sale fees reduce sale
proceeds. Positive balances are settled from the portfolio at year-end and at
the final observation. Remaining holdings are carried at their existing basis.
Losses expire at the close of each year. Monthly total returns represent gains
in accumulating funds. Net includes fees and paid tax. Gross excludes both.
Rolling efficiency continues to use model moments without simulating trades.

The response identifies `annual_net_realized_average_cost_v1` in
`reproducibility.tax_methodology`. Cash cost totals are in units of the selected
return basis per initial dollar. Annualized costs are pretrade NAV fractions
scaled by `12 / months`. The six cost keys are `total_commissions`,
`annualized_commissions`, `total_capital_gains_tax`,
`annualized_capital_gains_tax`, `total_costs`, and `annualized_total_costs`.
`tax_insolvent` and `unpaid_capital_gains_tax` disclose any unpaid balance.
Bootstrap trend results identify their fixed metric order in `metric_order`
and carry the original Validation fingerprint and tax methodology. See
[portfolio simulation](rebalancing-engine.md) for event and calendar conventions.

```json
{
  "instrument_ids": [1, 175, 243],
  "currency": "USD",
  "start_date": "1990-01-01",
  "end_date": "2026-08-31",
  "covariance_method": "cv_linear",
  "shrinkage_target": "diagonal",
  "risk_free_rate": 0.03,
  "return_basis": "nominal",
  "risk_free_source": "historical_bil",
  "objective": "gmv",
  "target_return": null,
  "asset_constraints": {
    "175": {"min": 0.1, "max": 0.7}
  },
  "group_constraints": {
    "Equity": {"min": 0.0, "max": 0.7},
    "US Stocks": {"min": 0.0, "max": 0.6},
    "Fixed Income": {"min": 0.0, "max": 0.7},
    "Commodities": {"min": 0.0, "max": 0.4}
  },
  "frontier_points": 51,
  "cv_folds": 4,
  "bootstrap_iterations": 500,
  "expected_block_length": 12,
  "random_seed": 42,
  "bootstrap_objectives": ["gmv", "max_sharpe"],
  "risk_aversions": null
}
```

These IDs are the actual local demonstration's VTI/TLT/GLD catalog IDs. Resolve
IDs through the existing instrument catalog in other databases.

| Field | Defaults and restrictions |
|---|---|
| instrument_ids | 2–15 distinct positive IDs; no silent removal |
| currency | USD only; no conversion |
| start_date/end_date | Optional; inclusive calendar months; reported history intersection |
| covariance_method | sample, ledoit_wolf, nonlinear, cv_linear; default sample |
| shrinkage_target | identity or diagonal for fixed-target CV Linear operations. Stationary Bootstrap selects the target within each synthetic Train |
| return_basis | nominal or real; default nominal; applies to every calculation |
| risk_free_source | historical_bil or fixed_annual; new default historical_bil |
| risk_free_rate | Manual fixed annual fraction; default 0.03 in fixed mode; finite in (-1,1] |
| objective | gmv, max_sharpe, target_return; default gmv |
| target_return | Required for target_return (including bootstrap_objectives), except for Resampled Frontier; minimum annual arithmetic fraction in [-12,12], in the selected return basis |
| asset_constraints | Up to 15 ID-keyed min/max bounds in [0,1] |
| group_constraints | Up to 30 explicit named min/max bounds in [0,1] |
| frontier_points | 2–201; default 51 |
| cv_folds | Even integers 2–20; default 4; portfolio folds 120/60 train/validation months, covariance folds 60/30 |
| bootstrap_iterations | 1–2,000, including odd counts, default 500. Resampled Frontier 1–500, default 250 |
| expected_block_length | 3–24 months; default 12 |
| random_seed | Integer 0 through 2^32−1; default 42 |
| bootstrap_objectives | Optional distinct list of gmv/max_sharpe/target_return (up to three) |
| risk_aversions | Optional one gamma in (0,1e6] per resampled frontier point |

For `/bootstrap`, an explicit `bootstrap_objectives` selects those objectives.
Otherwise an explicitly supplied `objective` selects that objective; when neither
is supplied, run both GMV and Max Sharpe. Target Return minimizes variance subject
to expected annual arithmetic return >= target, including when GMV already meets
the target. An infeasible target is returned as a completed analytical outcome,
not an HTTP validation error. See [target feasibility fields and example](target-return-bootstrap.md).
For `/resampled-frontier`, risk aversion defines the portfolios; `objective` is not
used to choose positions along the curve. All valid global objectives are accepted,
including `target_return` with a missing/null or unattainable target. `objective`,
`target_return` and `bootstrap_objectives` are retained in the saved API request but
do not enter curve construction. Target-presence validation applies only to the
operations that use it. Maximum work is 30,000 portfolio solves
(`frontier_points*bootstrap_iterations`) per resampled frontier job.

Invalid input, insufficient history or infeasible constraints return HTTP 422
before job submission. Capacity exhaustion returns HTTP 429 with Retry-After: 5.
Expired/unknown jobs return HTTP 404.

CV feasibility is operation-aware. `/cross-validation` checks every outer 120/60
fold and, for `cv_linear`, every inner 60/30 fold before starting a worker.
`/bootstrap` checks the same sizes and requires chronological Validation blocks
covering the entire panel. Its CV Linear target and intensity are fitted within
each synthetic Train. Other operations check full-sample covariance folds when
using `cv_linear`. Sample, Ledoit-Wolf and Nonlinear frontiers remain available
with 120 common months.
Insufficient-history errors include requested K, actual T, required minimum,
train/validation minima and eligible K. `/data-preview` reports separate
`eligible_portfolio_folds` and `eligible_covariance_folds` for the aligned history.

Both CV paths use `bidirectional_one_sided_block_kfold`; see [exact rules](analytics.md#bidirectional-one-sided-block-cross-validation).
Results disclose `training_direction`, one contiguous training segment per fold,
`total_observations`, `validation_observations`, `validation_coverage_fraction`,
`unvalidated_indices`, `minimum_portfolio_train/validation`,
`minimum_covariance_train/validation`, and `validation_interpretation`.
K=2 may leave central months unvalidated; different folds may overlap in training.
There is no embargo. Validation describes sample sensitivity and allocation
stability, not a historical investable backtest or independent forward performance.

## Job response and retrieval

```json
{
  "job_id": "opaque-generated-id",
  "kind": "frontier",
  "status": "running",
  "submitted_at": "ISO-8601 UTC timestamp",
  "elapsed_seconds": 0.01,
  "timeout_seconds": 300,
  "status_url": "/api/v1/analytics/jobs/opaque-generated-id",
  "result_url": "/api/v1/analytics/jobs/opaque-generated-id/result",
  "error": null
}
```

The values above illustrate the schema; observed timings and real calculated
results are in [the stored-data report](analytics-demo/report.md).

Poll `status_url`. Job states are running, completed, failed and timed_out.
The result URL returns the same descriptor plus `result`, null until a result is
available. A **completed job** can contain a numerical result with
`status=incomplete`; inspect both levels. `failed`/`timed_out` jobs include an
error and no fabricated calculation result.

Every successful result includes aligned period, asset ordering, source metadata,
original annual means/covariance, original correlation, numerical covariance
diagnostics, warnings and numerical execution seconds. Portfolio records have:

```text
status
weights: {instrument_id_string: allocation_fraction} | null
metrics: {
  expected_return, volatility, sharpe_ratio | null,
  historical_sharpe_ratio | null, risk_free_mean_return,
  historical_cagr, historical_max_drawdown
} | null
diagnostics: {solver_status, solver, iterations, timing, constraint_violation, ...}
```

| Result | Additional response data |
|---|---|
| Frontier | GMV, Max Sharpe, maximum return, selected objective, equal-weight feasibility/label, all target-return frontier portfolios |
| CV | Original full-sample portfolio, training/validation periods with training segments, every fold's weights/metrics/estimator diagnostics, ensemble, stability |
| Bootstrap | Methodology and sample fingerprints, fold geometry and replay diagnostics, per-objective joined Validation distributions, fold weights and success counts |
| Resampled frontier | Shared gamma values, averaged portfolios on original estimates, optional user portfolio on the original sample and its average return/volatility across successful samples, sample counts/failures, iteration solver/covariance diagnostics, conditional-bootstrap disclosure |

## Execution bounds

Each FastAPI application owns a process-local runner. Default: two concurrent
spawned processes, no waiting queue, 300-second deadlines, at most 32 retained
jobs, up to one hour of retention. At the storage limit, admitting a new job
evicts the earliest finished job. HTTP 429 indicates that the concurrent job
limit is reached. Numerical BLAS work is limited to one thread per worker.
The lifespan monitor checks deadlines every 0.25 seconds and terminates timed-out
workers; shutdown cleans up workers. Ordinary health/catalog requests stay available.
No ingestion runs inside analytics workers.

Settings: `ANALYTICS_WORKERS` (1–4), `ANALYTICS_TIMEOUT_SECONDS` (>0–1800),
`ANALYTICS_MAX_JOBS` (1–100), `ANALYTICS_RETENTION_SECONDS` (1–86400).
Jobs/results are deliberately not durable across application restarts. This local
integration uses one ASGI application process; multiple independent ASGI workers
would need routing to their owning registry or shared job persistence. No production
deployment or distributed infrastructure is introduced here.

## Reproduce the actual-data demonstration

Install the project with dev extras, then run with the configured project interpreter:

```powershell
.\.venv\Scripts\python.exe -m scripts.analytics_demo
```

The script requires actual stored VTI/TLT/GLD USD series, rejects missing/invalid
histories, and has no synthetic fallback. It calculates all four frontiers, four-fold one-sided block
CV, 1,000 stationary bootstrap samples for both objectives, and a 250×51 resampled
frontier. It also exercises all four API jobs through local FastAPI TestClient with
real spawned worker processes, records timing and dependency versions, verifies
the SQLite database byte hash is unchanged, and produces JSON/PNG/SVG artifacts.
`--skip-api` skips only the repeated API timing calculation.


## Nominal/Real and risk-free requests

New Live requests default to historical monthly BIL and nominal USD. Real mode
deflates the complete asset/BIL panel before estimation, optimization, CV and
Stationary Bootstrap. Estimated and historical Sharpe are reported separately.
Legacy fixed-rate snapshots retain their original semantics.

See [source, formula, refresh commands, API, revisions and verification](macro-analytics.md).

# Portfolio efficiency robustness API

For the current training-only portfolio comparison and paired Bootstrap output,
see [paired Validation API and methodology](paired-validation.md). The rolling
endpoint documented below is retained solely for compatibility with old clients.

Set `user_weights` (asset IDs as keys, weights totaling one) and
`include_efficiency_robustness: true` on `/cross-validation` or `/bootstrap` to
add fixed-allocation distance diagnostics without changing their existing
portfolio results. The distances are annual decimal fractions:
`return_shortfall` (max return at the user's volatility minus user return) and
`excess_volatility` (user volatility minus minimum volatility at user's return).

Submit the same analytics request to `POST /api/v1/analytics/rolling-efficiency`
for rolling 120-month windows stepped every 12 months. The endpoint returns the
standard asynchronous job contract. Optional `rolling_window_months` and
`rolling_step_months` default to 120 and 12. Each result reports dates,
covariance parameters, solver diagnostics, summary distribution and provenance.
When `covariance_method` is `cv_linear`, each rolling window selects the largest
eligible even fold count no greater than `cv_folds`.
The default 120-month window uses K=2, as specified for the rolling analysis.

### Fixed-portfolio Bootstrap metric distributions

With explicit `user_weights` and `include_efficiency_robustness=true`, `/bootstrap`
adds the following to `efficiency_robustness`:

- `horizon`: full selected panel `months`, `years`, `initial_wealth=1`,
  `sample_length_policy=full_selected_return_panel`. The path follows the
  requested rebalancing settings.
- `results[].portfolio_metrics`: `annualized_cagr`, `annualized_arithmetic_return`,
  `annualized_volatility`, `maximum_drawdown`, `historical_sharpe_ratio`,
  `terminal_wealth`, `calmar_ratio`, `sortino_ratio`, and trading costs.
- `distributions`: portfolio metrics plus `return_shortfall` and `excess_volatility`.
  Each includes `mean`, `median`, `standard_deviation`, `p5`, `p95`, `minimum`, `maximum`, `valid_count`,
  `unavailable_count`, `horizon_months`, `horizon_years`, `annualized`, and `units`.
- `metric_conventions` and `distribution_interpretation` document volatility
  and differential Sharpe sample standard deviations (ddof=1), null handling and
  statistical interpretation.

Returns, volatility, drawdown and efficiency distances are decimal fractions;
Sharpe is dimensionless and terminal wealth is the value from $1 in the selected
Nominal/Real basis. Every distribution uses the full selected duration T/12 years.
Drawdown and terminal wealth are full-horizon metrics rather than annual rates.
Per-metric counts disclose undefined Sharpe values and numerical overflow.
Path statistics remain available when covariance estimation or an efficiency
solver fails. The sampling fingerprints identify the versioned fold scenarios.

## Rebalancing simulation (engine 3.0)

New calculations simulate historical holdings with annual rebalancing and optional
self-financing commissions, including initial purchases. Model-based arithmetic
means/covariances remain unchanged. Historical reporting is net of costs with
gross results disclosed separately. See [portfolio simulation](rebalancing-engine.md) for conventions and legacy snapshot replay.

`POST /api/v1/analytics/rebalancing-optimization` adds the dedicated 21-rule
comparison job. See [request, response, charts and example](rebalancing-optimization.md).

Requests optionally accept typed `portfolio_controls` (`none`, `downside_vol`,
`trend`). See [configuration, alignment and separate response fields](portfolio-controls.md).

## Controlled Bootstrap comparison

`POST /api/v1/analytics/bootstrap` reports
`paired_comparison.user_portfolio_mode = "controlled"` when `portfolio_controls.mode`
is enabled. The optimizer uses net realized controlled Train volatility as its risk
budget (`risk_budget_source = "controlled_training_path"`). Each pair's `user`,
`differences` and Validation `clouds[].user` use the controlled Validation path.
Each Fold's weights come from its Train and remain the optimizer's target
throughout that Fold's Validation. The engine simulates the joined Validation
continuously.

`efficiency_robustness.user_portfolio_mode` identifies whether the all-generation
performance distributions describe the controlled or passive portfolio. Both gross
and net distributions are available. Passive and controlled results for each generated
dataset remain in `portfolio_control_samples`; failed controlled comparisons are
reported without a passive fallback. Legacy snapshots without the mode field retain
their original passive interpretation. Recalculate to obtain controlled comparisons.

For `resampled-frontier`, `user_portfolio_mode = "controlled"` means both
`user_portfolio.metrics` and `user_sample_average` use net controlled historical paths.
Their plotted `expected_return` is annual arithmetic path return; `volatility` is
realized annualized path volatility. The original marker has `status = "historical_path"`.
`user_sample_diagnostics` exposes successful samples, failed control samples with
reasons and excluded frontier samples. Control failures do not change frontier status.
The shared-risk-aversion curve remains model-based. Legacy results without the mode
field retain their existing passive markers; rerun to calculate controlled markers.

Stationary Bootstrap retains joined Validation indices for its separate Trend
Sensitivity job. Resampled Frontier uses full-panel draws and keeps its existing
curve construction. A legacy `bootstrap_control_cache` is eligible for reuse by
Resampled Frontier only when the data, settings and draw hashes match.
Resampled diagnostics include `cache_status`, `reused_samples`, and
`computed_samples`. See [the methodology version and replay recipe](bidirectional-stationary-bootstrap.md).

`POST /api/v1/analytics/rebalancing-optimization` requires
`portfolio_controls.mode = "none"` (the default). Active `downside_vol` or `trend`
controls return HTTP 422 before a job is submitted. Select the passive portfolio
mode to compare rebalancing rules.
