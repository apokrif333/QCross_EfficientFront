# One-sided CV upgrade verification

Implemented on the existing `main` working tree. Existing uncommitted analytics,
ingestion audit and UI work was preserved. No commit was created; the existing
HEAD is `2cfbb0158a02cb28091b97f1c9325d3cf95db412`.

## Method and isolation

Both portfolio CV and `select_shrinkage()` use
`app/analytics/splitting.py::one_sided_folds`. Supported K is even 2–20, default 4.
For K >= 4, consecutive validation blocks match `np.array_split`; training is
the longer single contiguous interval on one side. Equal lengths choose the
earlier interval. There is no shuffle, randomness, embargo or purge gap.
Different folds may overlap in training, but train and validation within a fold
never overlap. Portfolio minima are 120/60; covariance minima are 60/30 months.
Ordinary frontier optimization still supports 120 common months.

K=2 uses a complete two-block partition when feasible. Otherwise it uses the
largest equal outer validation windows leaving sufficient opposite-side training,
with explicit coverage and unvalidated central indices in the result.

Nested `cv_linear` selects shrinkage exclusively inside each extracted outer
training subset, refits on the complete training subset, estimates its training
mean, and optimizes there. Tests map inner indices back to the original panel and
assert that none access outer validation. Perturbing only the first outer
validation block preserves its shrinkage parameters, covariance diagnostics,
training metrics and optimized weights while changing its validation metrics;
the independent full-sample covariance can change.

The ensemble remains the arithmetic mean of all K fold weight vectors, with
unchanged convex constraints and stability metrics. A failed fold prevents a
partial ensemble. Validation is a sample-sensitivity and allocation-stability
diagnostic, not an investable historical backtest or independent forward result.

## Exact examples

Ranges below are inclusive. Zero-based indices correspond to the adjacent
one-based month ranges.

| T / K | Fold | Validation months | Validation indices | Training months | Training indices |
|---|---:|---|---|---|---|
| 180 / 2 | 1 | 1–60 | 0–59 | 61–180 | 60–179 |
| 180 / 2 | 2 | 121–180 | 120–179 | 1–120 | 0–119 |
| 240 / 4 | 1 | 1–60 | 0–59 | 61–240 | 60–239 |
| 240 / 4 | 2 | 61–120 | 60–119 | 121–240 | 120–239 |
| 240 / 4 | 3 | 121–180 | 120–179 | 1–120 | 0–119 |
| 240 / 4 | 4 | 181–240 | 180–239 | 1–180 | 0–179 |

For 180/2, coverage is 120/180 = 2/3; months 61–120 are unvalidated and reused in
both training windows. For 240/4, coverage is 240/240 = 1.

## API and frontend

Pydantic and Zod require even integer `cv_folds` in 2–20 and use default 4.
Operation-aware checks run before job submission and in the numerical paths.
`/cross-validation` checks outer portfolio and relevant inner covariance folds;
other operations check covariance CV only for `cv_linear`. Infeasible-history
errors include K, actual observations, required minimum, per-fold minima and
eligible K. Data preview exposes separate portfolio/covariance eligible K lists.

The selector displays only the supported numeric values and
uses actual aligned common-history counts to disable unavailable values for the
current operation. API submission also validates the request locally. Results
show one training segment, direction and validation coverage. Classical K-Fold
UI descriptions were replaced. Desktop and 390×844 mobile screenshots were
rendered and visually inspected, including all four bars and Ensemble after
responsive chart resizing.

## Validation commands and results

All Python commands used PyCharm's configured executable:
`H:\Miscellaneous\QCross_EfficientFront\.venv\Scripts\python.exe` (Python 3.14.6).

From the repository root:

```powershell
& 'H:\Miscellaneous\QCross_EfficientFront\.venv\Scripts\python.exe' -m pytest -q
# 272 passed, 1 opt-in network test deselected; 45.11 seconds.
& 'H:\Miscellaneous\QCross_EfficientFront\.venv\Scripts\python.exe' -m pytest -m network tests/integration -q
# 1 passed; 1.44 seconds. Read-only live source parsing; no historical database writes.
& 'H:\Miscellaneous\QCross_EfficientFront\.venv\Scripts\python.exe' -m ruff check app tests scripts
# All checks passed.
& 'H:\Miscellaneous\QCross_EfficientFront\.venv\Scripts\python.exe' -m scripts.analytics_demo
# All four covariance frontiers, CV, bootstrap, resampled frontier and real process API roundtrips completed.
git diff --check
# Passed.
```

From `ui`:

```powershell
npm test
# 23 tests passed in 5 files.
npm run build
# TypeScript and production Vite build passed; demo fixtures synchronized.
npm run format:check
# Passed.
$env:QCROSS_LIVE_TEST='1'
$env:QCROSS_API_URL='http://127.0.0.1:8001'
npx playwright test --config=.cv-playwright.config.ts
# 7 passed, including both opt-in live scenarios; 1.3 minutes.
```

The live test used a temporary configuration with dedicated API port 8001 and
Vite port 5174 to avoid replacing already running services. The configuration
was removed after verification. To reproduce the exact isolated command, create
`ui/.cv-playwright.config.ts` with:

```typescript
import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig({
  ...base,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5174' },
  webServer: [
    {
      command: 'H:\\Miscellaneous\\QCross_EfficientFront\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001',
      cwd: 'H:\\Miscellaneous\\QCross_EfficientFront',
      url: 'http://127.0.0.1:8001/health', reuseExistingServer: false, timeout: 30000,
    },
    {
      command: 'npm run dev -- --port 5174',
      url: 'http://127.0.0.1:5174', reuseExistingServer: false, timeout: 30000,
    },
  ],
});
```

The original random target-return success fixture had no common feasible target
across the new training subsets. Its synthetic 60-month blocks now share a mean
for that success case; the optimizer and historical data were unchanged and
infeasible-target/failed-fold tests remain. The live browser test explicitly
dismisses the date tooltip with Escape before clicking its adjacent preview
button. No tooltip behavior was changed in the application.

Final result: all tests passed, no unresolved implementation limitations. The
standard backend run deselected the opt-in network test, which passed separately.
Non-failing warnings concern existing Starlette/NumPy deprecations, Zod bundler
annotations and the existing Plotly bundle size. A backend process already
running engine 1.2.0 must be restarted to load the upgraded code; it was left
running and the live verification used an isolated current-engine process.

## Unrelated analytics and data preservation

Before implementation, saved bootstrap and resampled-frontier JSON results were
copied outside the repository. Comparing their regenerated sample-covariance
results, excluding execution/solver timings, engine/request reproducibility
metadata and the changed fold-count setting, yielded **73,126 numeric values
compared, maximum difference 0, zero differences**. Stationary Bootstrap sampling,
portfolio optimization and other covariance mathematics were unchanged.

Read-only demo regeneration confirmed the database SHA-256 before and after was
`6ee0af74a349065b8c82adf4ea9f4b6cd42136d8c8c129cbd3a17351ca4dfdfe`.
All saved demo calculation fingerprints were checked against current engine
version 1.3.0; all effective demo requests use K=4. The splitter is included in
the engine source fingerprint.

## Files changed or added for this task

Paths are relative to the repository root. Pre-existing unrelated changes are
excluded. Regenerated outputs with identical bytes are identified separately.

```text
README.md
app/analytics/splitting.py                              new
app/analytics/cross_validation.py
app/analytics/estimators.py
app/analytics/frontier.py
app/analytics/bootstrap.py                             default fold count only
app/analytics/provenance.py
app/api/routes/frontier.py
app/schemas/frontier.py
scripts/analytics_demo.py
tests/analytics/test_splitting.py                       new
tests/analytics/test_one_sided_estimators.py            new
tests/analytics/test_cross_validation.py
tests/analytics/test_deterministic.py
tests/analytics/test_api.py
ui/README.md
ui/src/App.tsx
ui/src/components/Settings.tsx
ui/src/components/Results.tsx
ui/src/types.ts
ui/src/lib/cv.ts                                       new
ui/src/lib/validation.ts
ui/src/lib/api.ts
ui/src/test/cv.test.tsx                                new
ui/e2e/cv.spec.ts                                      new
ui/e2e/lab.spec.ts
docs/analytics.md
docs/analytics-api.md
docs/one-sided-cv-upgrade.md                            new
docs/analytics-demo/bootstrap.json
docs/analytics-demo/cross-validation.json
docs/analytics-demo/frontier-cv_linear.json
docs/analytics-demo/frontier-ledoit_wolf.json
docs/analytics-demo/frontier-nonlinear.json
docs/analytics-demo/frontier-sample.json
docs/analytics-demo/frontiers.png
docs/analytics-demo/frontiers.svg
docs/analytics-demo/openapi.json
docs/analytics-demo/report.md
docs/analytics-demo/resampled-frontier.json
docs/analytics-demo/summary.json
docs/analytics-demo/weight-stability.svg
docs/ui-verification/README.md
docs/ui-verification/one-sided-cv-desktop.png            new
docs/ui-verification/one-sided-cv-mobile.png             new
docs/ui-verification/demo-desktop.png
docs/ui-verification/demo-mobile.png
docs/ui-verification/live-compare.png
docs/ui-verification/live-jobs.json
docs/ui-verification/asset-builder-live-desktop.png
docs/ui-verification/asset-builder-live-mobile.png
```

Also regenerated with identical bytes: `docs/analytics-demo/request.json` and
`docs/analytics-demo/weight-stability.png`. `npm run demo:sync` regenerated ignored
`ui/public/demo` files: bootstrap.json, cross-validation.json,
frontier-cv_linear.json, frontier-ledoit_wolf.json, frontier-nonlinear.json,
frontier-sample.json, resampled-frontier.json and manifest.json.
The supplied personal `del.py` was inspected and left unchanged.
