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
requires 95% successful iterations for GMV/Max Sharpe and is not a promise about future
performance. Target Return separates economic infeasibility from technical failures;
see the policy below.
Grouped negative-excess Max Sharpe can return a computational-limit failure when
exhaustive enumeration would exceed 300,000 active bases. API jobs are bounded,
process-local and not durable across restarts; the local API uses one ASGI process.
No frontend or production deployment was built.


## BIL/inflation upgrade verification

New Live requests default to historical monthly BIL and nominal USD. Real mode
deflates the complete asset/BIL panel before estimation, optimization, CV and
Stationary Bootstrap. Estimated and historical Sharpe are reported separately.
Legacy fixed-rate snapshots retain their original semantics.

See [source, formula, refresh commands, API, revisions and verification](macro-analytics.md).

The complete 2026-11-10 verification log, exact commands, file inventory,
local import proof and limitations are in [macro-verification.md](macro-verification.md).

## Target Return Bootstrap (engine 2.1.0)

Target Return now means minimum required annual arithmetic expected return
(`mean @ weights >= target`), consistently in Frontier, CV and Bootstrap. The
[methodology and API example](target-return-bootstrap.md) explain conditional
weight statistics, feasibility denominators, zero feasible samples and failure policy.

New deterministic coverage includes a complete default 1,000-iteration target run,
constrained maximum-return feasibility, numerical tolerance, a nonbinding GMV target,
all four covariance estimators in Nominal/Real mode, synchronized BIL indices,
technical failures separate from infeasibility, conditional quantiles, infeasible
original portfolios, and averages that fail the target on original estimates.
Real FastAPI worker jobs cover both return bases and both risk-free modes with
all three Bootstrap objectives. UI tests cover selection, feasibility counts,
shortfall and snapshots with absent original portfolios and null stability.

Changed/new files for this upgrade:

```text
README.md
app/analytics/bootstrap.py
app/analytics/optimizer.py
app/analytics/provenance.py
app/api/routes/frontier.py
app/schemas/frontier.py
docs/analytics.md
docs/analytics-api.md
docs/analytics-verification.md
docs/target-return-bootstrap.md
tests/analytics/test_target_bootstrap.py
tests/analytics/test_target_bootstrap_api.py
ui/src/App.tsx
ui/src/types.ts
ui/src/lib/validation.ts
ui/src/components/Settings.tsx
ui/src/components/Results.tsx
ui/src/test/target-bootstrap.test.tsx
```

Verification commands run from repository root unless noted:

```powershell
.venv\Scripts\python.exe -m pytest --basetemp=./.target-final-testtmp -o cache_dir=./.target-pytest-cache --tb=short -q
.venv\Scripts\python.exe -m ruff check app tests scripts
cd ui
npm.cmd test
npm.cmd exec -- tsc -b
npm.cmd run build
npm.cmd exec -- prettier --check src
```

Results on 2026-11-10: **342 passed, 1 skipped, 1 deselected** in 136.90 seconds
for the complete backend suite. The skip is unavailable Windows symlink creation;
the deselected test is the opt-in live network test. Ruff: all checks passed.
Vitest: **29 passed across 7 files**. TypeScript, Vite production build and Prettier
checks passed. Existing dependency deprecations (Starlette/httpx, NumPy matlib),
Rollup annotations in Zod and the Plotly bundle-size advisory remain non-failing.

No database schema, source observations, ingestion, one-sided splitting or
Stationary Bootstrap sampling mechanism changed. No live-source download or
production deployment is required or claimed for this upgrade. The numerical
bootstrap failure policy is intentionally stricter for Target Return: any technical
failure suppresses its aggregate, while infeasible targets do not fail computation.

## Target Return / Resampled Frontier interaction (engine 2.1.1)

Resampled Frontier now accepts every supported global objective, including
Target Return with no target or an infeasible target. Operation-aware API, client
and snapshot validation require a target only where it is used. Shared risk-aversion
optimization, stationary indices, averaging, covariance estimation, constraints,
Nominal/Real and BIL calculations remain unchanged. The App forwards explicit
`bootstrap_objectives` unchanged, including mixed Target Return/GMV requests.

Regression tests compare identical curve weights, metrics, sampled indices and API
calculation fingerprints across all global objectives in Nominal/Real modes. A
guard test fails if Resampled Frontier ever calls target-return or Max Sharpe
optimization. API worker tests, frontend payload/snapshot tests and real browser
App tests cover a missing target and explicit Bootstrap objective lists. All prior
Target Return Bootstrap feasibility/failure/conditional-statistics tests still pass.

Commands executed on 2026-11-10:

```powershell
git -c safe.directory=H:/Miscellaneous/QCross_EfficientFront fetch origin main
.venv\Scripts\python.exe -m pytest --basetemp=./.interaction-full-tmp -o cache_dir=./.interaction-pytest-cache --tb=short -q
.venv\Scripts\python.exe -m ruff check app tests scripts
cd ui
npm.cmd test
npm.cmd run build
npm.cmd exec -- tsc -b
npm.cmd exec -- prettier --check src e2e/target-objectives.spec.ts
$env:QCROSS_LIVE_TEST = '1'
npm.cmd exec -- playwright test --config=playwright.macro.config.ts
```

Results: backend **349 passed, 1 skipped, 1 deselected** in 140.19 seconds; Vitest
**33 passed in 8 files**; Playwright/Chrome **11 passed** including Live API jobs,
four-method comparison, Real BIL, snapshot replay and the two new App regressions.
The backend skip is unavailable Windows symlinks; the live ingestion network test
is opt-in and was excluded. Ruff, TypeScript, Vite and Prettier passed. The existing
non-failing dependency deprecations, Zod annotation and Plotly-size advisories remain.
No market data or ingestion was modified, and no production deployment is claimed.
