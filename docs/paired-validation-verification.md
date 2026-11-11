# Paired Validation verification

Local verification; no production data was modified. Baseline remote main was
399fed0790b901974b6d0bd86105a2c3f5a5d1f1, inspected with git ls-remote before editing.

## Commands and outcomes

- `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider`: 408 passed,
  1 skipped (Windows symlink unavailable), 1 deselected (opt-in network test),
  159.41 seconds. Includes ingestion, storage, analytics and async API jobs.
- `.venv/Scripts/python.exe -m pytest tests/analytics/test_paired_comparison.py tests/analytics/test_cross_validation.py tests/analytics/test_bootstrap.py -q -p no:cacheprovider`:
  42 passed after the final CV integration changes, 25.02 seconds.
- `.venv/Scripts/python.exe -m ruff check app tests`: passed.
- `.venv/Scripts/python.exe -m ruff format --check app tests`: 113 files formatted.
- `cd ui; npm test`: 47 passed.
- `cd ui; npm run build`: TypeScript and Vite production build passed.
- `cd ui; npm run format:check`: passed.
- `cd ui; $env:PLAYWRIGHT_CHANNEL='chrome'; npm exec playwright test`:
  10 passed, 5 skipped (opt-in Live tests), 14.0 seconds. Includes actual Plotly
  density rendering and legend toggles, legacy snapshots and prior charts.
- `test_500_datasets_have_250_pairs_and_500_user_paths`: 500 full-length
  240-month, three-asset synthetic datasets, 250 successful comparisons,
  500 user path observations per defined metric; measured 4.777 seconds.

Warnings remain from Starlette/httpx, numpy.matlib, and disclosed inaccurate
solver attempts in existing stress tests. Vite reports its existing bundle-size
and third-party annotation warnings. No failing tests remain in the executed
commands. Live browser scenarios and network ingestion were not run; synthetic
async API tests cover both Nominal and Real with historical BIL fixtures.

Legacy rolling numerical code and snapshot schemas are retained for backward
compatibility. The rolling launch/display is removed from the active interface.
The paired reference frontier uses empirical Validation covariance while Train
uses the selected estimator, including conditional cv_linear Bootstrap.

## Changed and new files

- `README.md`
- `app/analytics/bootstrap.py`
- `app/analytics/cross_validation.py`
- `app/analytics/frontier.py`
- `app/analytics/models.py`
- `app/analytics/optimizer.py`
- `app/analytics/paired_comparison.py`
- `app/analytics/provenance.py`
- `app/api/routes/frontier.py`
- `app/schemas/frontier.py`
- `docs/analytics-api.md`
- `docs/analytics.md`
- `docs/paired-validation-verification.md`
- `docs/paired-validation.md`
- `tests/analytics/test_api.py`
- `tests/analytics/test_bootstrap_portfolio_metrics.py`
- `tests/analytics/test_paired_comparison.py`
- `ui/e2e/efficiency.spec.ts`
- `ui/e2e/paired.spec.ts`
- `ui/src/App.tsx`
- `ui/src/components/PairedComparisonView.tsx`
- `ui/src/components/Results.tsx`
- `ui/src/components/Settings.tsx`
- `ui/src/lib/snapshot.ts`
- `ui/src/lib/validation.ts`
- `ui/src/test/efficiency.test.tsx`
- `ui/src/test/paired.test.tsx`
- `ui/src/types.ts`
