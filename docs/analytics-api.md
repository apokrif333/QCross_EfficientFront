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
    cross_validation.py    # classical K-fold portfolios and CV ensemble
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

```json
{
  "instrument_ids": [1, 175, 243],
  "currency": "USD",
  "start_date": "1990-01-01",
  "end_date": "2026-08-31",
  "covariance_method": "cv_linear",
  "shrinkage_target": "diagonal",
  "risk_free_rate": 0.03,
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
  "cv_folds": 5,
  "bootstrap_iterations": 1000,
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
| shrinkage_target | identity or diagonal; used for cv_linear |
| risk_free_rate | Explicit annual fraction, default 0.03; finite in [-1,1] |
| objective | gmv, max_sharpe, target_return; default gmv |
| target_return | Required for target_return; annual arithmetic fraction in [-12,12] |
| asset_constraints | Up to 15 ID-keyed min/max bounds in [0,1] |
| group_constraints | Up to 30 explicit named min/max bounds in [0,1] |
| frontier_points | 2–201; default 51 |
| cv_folds | 2–10; default 5; outer portfolio training must have 120 months |
| bootstrap_iterations | 1–2,000; default 1,000; resampled frontier 1–500, default 250 |
| expected_block_length | 3–24 months; default 12 |
| random_seed | Integer 0 through 2^32−1; default 42 |
| bootstrap_objectives | Optional distinct list of gmv/max_sharpe |
| risk_aversions | Optional one gamma in (0,1e6] per resampled frontier point |

For `/bootstrap`, an explicit `bootstrap_objectives` selects those objectives.
Otherwise an explicitly supplied `objective` selects that objective; when neither
is supplied, run both GMV and Max Sharpe. Target-return bootstrap is rejected.
For `/resampled-frontier`, risk aversion defines the portfolios; `objective` is not
used to choose positions along the curve. Maximum work is 30,000 portfolio solves
(`frontier_points*bootstrap_iterations`) per resampled frontier job.

Invalid input, insufficient history or infeasible constraints return HTTP 422
before job submission. Capacity exhaustion returns HTTP 429 with Retry-After: 5.
Expired/unknown jobs return HTTP 404.

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
  historical_cagr, historical_max_drawdown
} | null
diagnostics: {solver_status, solver, iterations, timing, constraint_violation, ...}
```

| Result | Additional response data |
|---|---|
| Frontier | GMV, Max Sharpe, maximum return, selected objective, equal-weight feasibility/label, all target-return frontier portfolios |
| CV | Original full-sample portfolio, training/validation periods with training segments, every fold's weights/metrics/estimator diagnostics, ensemble, stability |
| Bootstrap | Results by objective, original/resampled portfolios, weight/displacement statistics, all iteration records, success/failure counts and reasons, conditional-bootstrap disclosure and sample fingerprint |
| Resampled frontier | Shared gamma values, averaged portfolios on original estimates, sample counts/failures, all iteration solver/covariance diagnostics, conditional-bootstrap disclosure |

## Execution bounds

Each FastAPI application owns a process-local runner. Default: two concurrent
spawned processes, no waiting queue, 300-second deadlines, at most 32 retained
jobs, one-hour retention. Numerical BLAS work is limited to one thread per worker.
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
histories, and has no synthetic fallback. It calculates all four frontiers, five-fold
CV, 1,000 stationary bootstrap samples for both objectives, and a 250×51 resampled
frontier. It also exercises all four API jobs through local FastAPI TestClient with
real spawned worker processes, records timing and dependency versions, verifies
the SQLite database byte hash is unchanged, and produces JSON/PNG/SVG artifacts.
`--skip-api` skips only the repeated API timing calculation.
