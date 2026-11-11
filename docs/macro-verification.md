# Macro analytics verification — 2026-11-10

Implementation and methodology: [macro-analytics.md](macro-analytics.md).
Import evidence: [macro-import-verification.json](macro-import-verification.json).

## Commands and outcomes

All Python invocations used the project `.venv/Scripts/python.exe` (Python 3.14).
Commands below are run from the repository root unless stated otherwise.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q --basetemp=./.macro-testtmp-final -o cache_dir=./.macro-pytest-cache --tb=short
# 314 passed, 1 skipped, 1 deselected, 109.95 seconds.
# The skip is unavailable Windows symlink creation; the deselection is the opt-in network test.
& .\.venv\Scripts\python.exe -m ruff check app tests scripts alembic/versions/0006_macro_references.py --output-format concise
# All checks passed.
& .\.venv\Scripts\python.exe -m app.cli.macro validate-bil-inflation --html tests/fixtures/lazyportfolio_bil_inflation.html
# 2806 observations, zero pending revisions.
& .\.venv\Scripts\python.exe -m app.cli.macro status
# Inflation: 2806 months, 1793-01–2026-10. BIL pointer: 2805 months, 1793-01–2026-09.
& .\.venv\Scripts\python.exe -m scripts.import_macro_verified
# Completed local SQLite migration/import; nominal content hashes identical before/after.
```

SQLite upgrade/downgrade/upgrade was also executed on `data/macro-validation.db`,
a disposable copy created using SQLite backup. The production local file was
upgraded/imported only after successful preview and migration verification.

```powershell
$env:DATABASE_URL='sqlite:///./data/macro-validation.db'
& .\.venv\Scripts\python.exe -m alembic downgrade 0005_return_import_status
& .\.venv\Scripts\python.exe -m alembic upgrade head
$env:DATABASE_URL='postgresql+psycopg://offline:offline@localhost/offline'
& .\.venv\Scripts\python.exe -m alembic upgrade 0005_return_import_status:head --sql
# PostgreSQL SQL generation passed; no server connection is claimed.
```

From `ui`:

```powershell
npm.cmd run build
# TypeScript and Vite production build passed.
npm.cmd test
# 26 tests passed in 6 files.
$env:QCROSS_LIVE_TEST='1'
npx.cmd playwright test --config=playwright.macro.config.ts
# 9 passed in 1.7 minutes, including actual local API jobs and snapshot replay.
npx.cmd playwright test --config=playwright.macro.config.ts e2e/macro.spec.ts
# Final targeted verification after fixing stale-result property-order comparison:
# 2 passed in 11.5 seconds.
```

The isolated browser configuration starts API port 8002 and Vite port 5175. On
Windows it uses the already installed Chrome. Initial browser attempts could
not locate a Playwright-managed browser in the sandbox; those were superseded
by the successful installed-Chrome runs. Test fixtures were updated to explicitly
request their legacy fixed-rate semantics and original 2026-08 endpoint. The
ordinary CV splitter and Stationary Bootstrap index generator are unchanged.

## Evidence

- Original live LazyPortfolioETF BIL source fetched 2026-11-10, retained as
  `tests/fixtures/lazyportfolio_bil_inflation.html`; 2806 return months and 2807
  capital entries. No synthetic source observations were added.
- Local inflation storage: 1440 `historical_reconstruction`, 1364 `published`,
  2 `assumed_zero_unpublished` (2025-10 and 2026-10).
- All 503 nominal series / 361179 monthly observations retained their content
  SHA-256 `207810e3293e35c79a00d598d4726dd5759edf2e35ad9e1598d6bc02fee16080`.
- Pinned official BLS fixture verifies 45 independent CPI pairs, separated into
  historical NSA and SA periods; tolerance 0.00005 percentage points. This does
  not claim re-verification of the user's earlier 1283-pair study or modern vintages.
- Both bases, both risk-free modes, all four job endpoints, all four estimators,
  differential objective equality, BIL as a selected asset, exact Decimal ratio,
  source rejection, revisions, idempotence, fingerprints, nested CV isolation and
  bootstrap joint-index synchronization are covered by deterministic tests.
- Desktop and 390×844 mobile views of actual Real/BIL results were visually inspected.
  Both Sharpe columns, actual asset moments, the BIL differential label and the
  compact basis control are visible. The fresh result has no stale-result warning.
- Settings controls were also checked with an explicitly mocked catalog/preview.
  Generated screenshots were removed during cleanup; future test screenshots are
  written to the ignored Playwright test-results directory.

## Limits

No live PostgreSQL migration or Railway deployment was performed. The stored
nominal BIL series ends in September 2026; updating its October observation
remains the established nominal-ingestion workflow, not the inflation importer.
Unpublished exceptions require maintained publication confirmation; numerical
zeros are not a reliable publication detector. No new public CPI provider is
used by production analytics. The optional network VTI integration test was
deselected; live BIL fetching was independently performed successfully.

## Reviewed fixed-rate call sites

`schemas/frontier.py` distinguishes explicit legacy rates from new BIL requests.
`routes/frontier.py` attaches references before submission and preview.
`metrics.py` retains the legacy fixed nominal estimated Sharpe and adds observed
monthly compounded fixed-rate historical Sharpe. `optimizer.py` delegates to
differential moments when supplied, otherwise preserves the original fixed-rate
solver. `frontier.py`, `cross_validation.py`, and `bootstrap.py` pass differential
moments/benchmarks from the selected training/sample rows. `jobs.py` receives
the prepared panel once and preserves job/status contracts. `analytics_demo.py`
and pre-existing Demo JSON remain explicitly legacy nominal/fixed calculations.


## Changed/new file inventory

```text
.gitattributes
README.md
alembic/versions/0006_macro_references.py
app/analytics/bootstrap.py
app/analytics/constraints.py
app/analytics/cross_validation.py
app/analytics/data_loader.py
app/analytics/estimators.py
app/analytics/frontier.py
app/analytics/metrics.py
app/analytics/models.py
app/analytics/optimizer.py
app/analytics/provenance.py
app/analytics/references.py
app/api/routes/frontier.py
app/cli/macro.py
app/cli/main.py
app/db/models/__init__.py
app/db/models/reference.py
app/ingestion/lazyportfolio/inflation.py
app/ingestion/lazyportfolio/macro_service.py
app/schemas/frontier.py
docs/analytics-api.md
docs/analytics-verification.md
docs/analytics.md
docs/macro-analytics.md
docs/macro-import-verification.json
docs/macro-verification.md
docs/ui-verification/README.md
scripts/import_macro_verified.py
tests/analytics/test_api.py
tests/analytics/test_macro.py
tests/analytics/test_macro_storage.py
tests/analytics/test_provenance.py
tests/fixtures/cpi_reference.json
tests/fixtures/lazyportfolio_bil_inflation.html
ui/README.md
ui/e2e/asset-builder.spec.ts
ui/e2e/lab.spec.ts
ui/e2e/macro.spec.ts
ui/playwright.macro.config.ts
ui/src/App.tsx
ui/src/components/PortfolioTable.tsx
ui/src/components/Results.tsx
ui/src/components/Settings.tsx
ui/src/lib/api.ts
ui/src/lib/display.ts
ui/src/lib/snapshot.ts
ui/src/lib/validation.ts
ui/src/test/macro.test.tsx
ui/src/types.ts
```
