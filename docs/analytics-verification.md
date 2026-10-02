# Verification record — 2026-10-02

Implementation proceeded in the requested order. Stage 2 began only after the
complete configured pytest suite and Ruff passed for Stage 1; Stage 3 began only
after both passed for Stage 2. API integration followed Stage 3 verification.

| Checkpoint | Complete configured pytest suite | Duration | Ruff |
|---|---|---:|---|
| Existing backend baseline | 152 passed, 1 deselected | 19.23 s | — |
| Stage 1 deterministic engine | 161 passed, 1 deselected | 20.36 s | All checks passed |
| Stage 2 classical CV | 167 passed, 1 deselected | 20.93 s | All checks passed |
| Stage 3 stationary bootstrap | 178 passed, 1 deselected | 21.58 s | All checks passed |
| API integration | 186 passed, 1 deselected | 29.77 s | All checks passed |
| Final diagnostics/reference tests | 191 passed, 1 deselected | 29.27 s | All checks passed |

Commands used the PyCharm-configured Python 3.14.6 interpreter at
`.venv/Scripts/python.exe`, with `-m pytest -q` and `-m ruff check .`.
`git diff --check` also passed. No ingestion code, SQLAlchemy models, migrations,
historical observations or validated return-series metadata were modified.

The repository's default pytest configuration excludes its one opt-in `network`
test. It was left excluded to honor the instruction not to redownload histories.
All existing offline ingestion/storage/API/migration tests continue to pass.
All new unit datasets are explicitly synthetic; the final demonstration uses
actual stored USD histories only.

Two dependency deprecation warnings remain: Starlette's existing httpx TestClient
integration, and numpy.matlib inside the independent pinned nonlinear-shrinkage
reference package. Neither indicates a failed numerical or integration test.

## Independent checks

* Sample covariance against NumPy `ddof=1` and Ledoit-Wolf against scikit-learn.
* Analytical nonlinear shrinkage against independent `nonlinshrink==0.7` across
  several spectra, after reviewing the authors' original MATLAB algorithm.
* Gaussian validation NLL against SciPy's multivariate-normal log density.
* GMV and positive Max Sharpe against independent analytical diagonal-covariance
  solutions; constrained two-asset Sharpe against scalar numerical optimization.
* Risk-aversion utility portfolios against a differentiated analytical solution.
* Negative-excess Sharpe on unconstrained and group-constrained polytopes.
* Consecutive folds, disjoint train/validation, minimum history, nested tuning,
  direct weight averages, constraint preservation and repeated-run reproducibility.
* Stationary geometric blocks, circular wrapping, joint row resampling, exact
  sample length, deterministic seeds and matching estimator sample fingerprints.
* Independently calculated weight statistics and allocation distances, conditional
  shrinkage tuning, shared-gamma averaging, individually recorded failures and
  the exact 95% acceptance boundary.
* Real spawned-process API results, bounded capacity, timeouts, worker failures,
  ordinary health availability and validation before submission.

## Actual stored-data verification

See [the generated report](analytics-demo/report.md) and
[timing/provenance summary](analytics-demo/summary.json). VTI/TLT/GLD use 440 aligned
USD months from 1990-01 to 2026-08. All four classical frontiers have 51 successful
points. Five-fold CV has five successful portfolios and an ensemble. GMV and Max
Sharpe each have 1,000/1,000 successful stationary bootstrap iterations. The optional
resampled frontier has 250/250 successful samples and 51 shared risk-aversion points.

Both direct mathematical calls and the four local FastAPI job endpoints completed
with real worker processes. Numerical times, API submission/result retrieval times
and end-to-end local job timings are reported separately. The PNG charts were
visually inspected; vector SVG versions are also supplied.

The SQLite database's SHA-256 before and after the final demonstration is identical:

```text
6ee0af74a349065b8c82adf4ea9f4b6cd42136d8c8c129cbd3a17351ca4dfdfe
```

## Explicit limits

Analytical nonlinear shrinkage rejects rank-deficient inputs rather than changing
methods. Every accepted covariance transformation is disclosed. Bootstrap acceptance
requires 95% successful iterations and is not a promise about future performance.
Grouped negative-excess Max Sharpe can return a computational-limit failure when
exhaustive enumeration would exceed 300,000 active bases. API jobs are bounded,
process-local and not durable across restarts; the local API uses one ASGI process.
No frontend or production deployment was built.
