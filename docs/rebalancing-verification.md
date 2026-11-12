# Rebalancing implementation verification

The starting local main and remote main were both
`afdbf101034c5dff90a3b698c84bd68052565418` when inspected. Stage 1 was verified and
committed separately as `3717b264fd5a62a861d50370bcf285bf516ba6ea` before building
the Stage 2 job and interface. Stage 2 follows that commit on main.

## Implemented behavior

- One holdings simulator supplies annual historical portfolio paths to Frontier,
  CV, Bootstrap and paired comparisons. Optimizer mean/covariance assumptions,
  objectives, constraints and all four estimators retain their definitions.
- Costs apply to initial purchases and actual net buys/sells, with self-financing
  execution. Gross and net statistics are separate. Initial purchase costs are
  also included in drawdown, even when the first month's gains recover them.
- Historical volatility is sample standard deviation times sqrt(12). Existing
  model volatility remains separately reported. Each validation sample starts
  its own allocation; CV directions and Bootstrap indices/pairing are unchanged.
- The new asynchronous job compares all 20 rules. Its table, thirteen-point 3D
  threshold curve, seven-point calendar comparison, equity/weight charts and
  trade journal use actual simulator outputs. The annual baseline is highlighted.
- Legacy snapshots remain readable and replay with monthly rebalancing and zero
  costs when their original request omitted these settings. New requests default
  to annual rebalancing and zero costs. Engine identity is now 3.1.0.
- No database migration, historical-return rewrite, ingestion change, Downside
  Volatility, SMA/Momentum or hedge implementation is included.

See [simulation conventions](rebalancing-engine.md) and
[API, chart interpretation and a reproducible four-rule example](rebalancing-optimization.md).

## Commands and observed results

Commands run from the repository root unless a UI working directory is stated:

```powershell
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --tb=short
.venv\Scripts\python.exe -m pytest tests/analytics/test_cross_validation.py tests/analytics/test_portfolio_simulator.py -q -p no:cacheprovider --tb=short
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m ruff format --check app tests
```

Final full backend: 445 passed, 1 skipped (Windows symlink permission), 1 deselected
(opt-in network test), in 182.36 seconds. The additional CV/simulator check after unifying initial
cost drawdown: 19 passed. Ruff lint passed; 118 files passed the format check.
The full suite includes ingestion, database/migration, async API jobs, all
estimators, both return bases, legacy results, Bootstrap sampling and pairing.
There were NumPy deprecation and existing solver accuracy warnings; no failed
assertions in these final checks.

```powershell
# Working directory: ui
npm run build
npm test
npm run format:check
$env:PLAYWRIGHT_CHANNEL='chrome'; npm exec playwright test
```

TypeScript plus Vite production build passed. Vitest: 50 passed in 11 files.
Prettier passed. Playwright with Chrome: 11 passed, 5 skipped Live scenarios
requiring a separately connected backend. The new browser scenario verifies an
actual WebGL scatter3d with 13 points, 20 strategy rows, gross/net switching,
weights, event rendering and snapshot import. Build warnings concern Plotly
chunk sizes and upstream Zod annotations. Both Plotly partial bundles are lazy
imports; no production deployment or live database refresh is claimed.

The new frontend fixture invokes the real backend simulator on synthetic data.
UI tests therefore require the project's Python dependencies; they use the root
`.venv` when present, otherwise `QCROSS_PYTHON` or the system Python interpreter.
Production UI builds do not execute this fixture.

## Performance measurement

A local synthetic 240-month, three-asset panel with fixed 60/20/20 weights,
aligned time-varying synthetic BIL and a 0.1% commission was evaluated with one
numerical thread. Stationary Bootstrap retained all 500 generated datasets,
250 valid validation pairs and 500 user-portfolio path observations. It finished
in **10.684 seconds** with status complete. Comparing all 20 rebalancing rules
on the same panel took **0.095 seconds**. These are local synthetic measurements,
not production guarantees; the unchanged worker default timeout is 300 seconds.

The existing deterministic performance regression can be rerun with:

```powershell
.venv\Scripts\python.exe -m pytest tests/analytics/test_paired_comparison.py::test_500_datasets_have_250_pairs_and_500_user_paths -q -s -p no:cacheprovider
```

## Changed files

The following lists cover both stages relative to the inspected starting main.

```text
README.md
app/analytics/bootstrap.py
app/analytics/bootstrap_metrics.py
app/analytics/cross_validation.py
app/analytics/frontier.py
app/analytics/jobs.py
app/analytics/metrics.py
app/analytics/models.py
app/analytics/paired_comparison.py
app/analytics/portfolio_simulator.py
app/analytics/provenance.py
app/analytics/rebalancing.py
app/api/routes/frontier.py
app/schemas/frontier.py
docs/analytics-api.md
docs/analytics.md
docs/rebalancing-engine.md
docs/rebalancing-optimization.md
docs/rebalancing-verification.md
tests/analytics/test_api.py
tests/analytics/test_bootstrap_portfolio_metrics.py
tests/analytics/test_cross_validation.py
tests/analytics/test_deterministic.py
tests/analytics/test_macro.py
tests/analytics/test_paired_comparison.py
tests/analytics/test_portfolio_simulator.py
tests/analytics/test_rebalancing.py
tests/analytics/test_target_bootstrap.py
tests/analytics/test_user_portfolio.py
ui/e2e/rebalancing.spec.ts
ui/package-lock.json
ui/package.json
ui/src/App.tsx
ui/src/components/Plot.tsx
ui/src/components/RebalancingView.tsx
ui/src/components/Results.tsx
ui/src/components/Settings.tsx
ui/src/env.d.ts
ui/src/lib/cv.ts
ui/src/lib/snapshot.ts
ui/src/lib/validation.ts
ui/src/styles.css
ui/src/test/rebalancing.test.tsx
ui/src/test/rebalancingFixture.ts
ui/src/types.ts
```
