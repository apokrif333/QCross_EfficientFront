# Portfolio efficiency robustness verification

Verified locally on 2026-11-11. Changes remain uncommitted and unpublished.

## Checks

| Check | Result |
| --- | --- |
| Backend suite | 392 passed, 1 skipped, 1 deselected; 132.69 seconds |
| Frontend unit tests | 41 passed in 9 files |
| TypeScript and production build | Passed (`tsc -b` and Vite) |
| Playwright with installed Chrome and Live API | 15 passed; 2.1 minutes |
| Ruff lint and formatting | Passed |
| Prettier | Passed |

Backend command:

```powershell
.venv\Scripts\python.exe -m pytest -q -o junit_family=xunit1 --junitxml=.efficiency-extension-tests.xml
```

The temporary XML was used to read the measured performance property and is
removed after verification. The skipped storage test requires Windows symlink
privileges. The deselected live-provider test is excluded by the repository's
default `-m 'not network'` setting. Remaining warnings concern existing
dependency deprecations and the sandbox's unwritable pytest cache.

Frontend commands (inside `ui`):

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run format:check
$env:PLAYWRIGHT_CHANNEL='chrome'
$env:QCROSS_LIVE_TEST='1'
$env:QCROSS_API_URL='http://127.0.0.1:8011'
$env:QCROSS_UI_PORT='5177'
npm.cmd run test:e2e
```

The full browser run verifies fixed-weight Bootstrap metrics in both Nominal and
Real mode with 1,000 samples of 240 months, all eight distribution rows and their
20-year horizon labels. It also retains the separate 120-month rolling checks.

Lint commands (repository root):

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
git -c safe.directory=H:/Miscellaneous/QCross_EfficientFront diff --check
```

The UI server used port 5177 and the local API used port 8011 to avoid reusing
unrelated servers already listening on the default ports. The API's SQLite
connection used `mode=ro&immutable=1`; ingestion was not run. Temporary server
code is removed after verification. Browser screenshots and traces stay under
ignored `ui/test-results/`, not documentation assets.

## Numerical and integration coverage

- Closed-form return shortfall and excess volatility, efficient allocations,
  negative expected returns and zero covariance.
- Explicit solver errors, materially negative distances, nonfinite allocations,
  indefinite covariance, asset/group constraints and fixed user weights.
- All four covariance estimators in nominal and transformed real returns.
- Complete rolling windows, 12-month steps, per-window failures and fingerprints
  sensitive to allocations, constraints and return basis.
- CV training/validation isolation and reuse of training-selected `cv_linear`
  parameters for the retrospective validation benchmark.
- Exact shared Bootstrap samples and covariance reuse; optional efficiency
  failures do not change the existing optimization outcomes. Efficiency can
  still be evaluated when differential covariance estimation fails.
- Async rolling API jobs, input errors, new snapshot round trips and existing
  legacy snapshot compatibility.
- Actual Live Real rolling analysis on exactly 120 months with `cv_linear`,
  effective K=2 and fixed user allocation, before any CV result is calculated.
- Independently calculated CAGR, arithmetic return, historical volatility,
  drawdown, differential Sharpe and terminal wealth on shared Bootstrap indices
  under all four estimators and both return bases. Metric aggregation includes
  minimum and maximum alongside mean, median and P5/P95.
- Full Bootstrap sample lengths of 180, 240 and 360 months, exact sampling-call
  counts, aligned BIL observations, undefined Sharpe, total loss and wealth
  overflow. Path statistics remain available after frontier/estimator failures.

Tests uncovered and corrected default-setting comparisons that incorrectly
marked fresh results as stale, the rolling button's dependency on a prior CV
result, literal Unicode escapes in table headings, and charts connecting
across failed windows. Invalid snapshot distance values are now rejected.

## Performance

`tests/analytics/test_efficiency_performance.py` runs 1,000 actual Stationary
Bootstrap iterations with GMV, Max Sharpe and Target Return, plus both exact
efficiency-distance problems and the six fixed-portfolio path metrics for every
sample. The deterministic fixture has
3 assets and 240 monthly observations; seed=42, mean block length=12, sample
covariance, target=0.05 and fixed equal user weights.

Measured runtime in the full backend run: **14.282658 seconds**.
All **1,000** efficiency evaluations were valid. This measurement is for that
fixture; it is not a runtime guarantee for larger asset/group configurations.
