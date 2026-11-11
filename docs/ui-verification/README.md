# Portfolio Lab browser verification

Latest macro upgrade (2026-11-10): 9 Playwright scenarios passed, followed by 2
targeted final checks of Real/BIL results and legacy defaults. Desktop and mobile
views were visually inspected; generated screenshots were removed during cleanup.
See [exact verification commands and evidence](../macro-verification.md).

Verified locally in Chromium on 2026-10-06; no public deployment.

- Desktop Frontier and four-method comparison were inspected using stored USD results.
- At 390×844, the document had no horizontal overflow; wide tables scrolled internally.
- 15 completed jobs were observed through UI requests/status polling/result retrieval,
  including all four analytics endpoints, four-method comparison, and seven snapshot replays.
- The asset editor, groups 1/3/5, a 100% VTI reference and constraint violation
  were checked against real stored 1793-01—2026-08 history, 2,804 months.

Live observations: VTI (1), TLT (175), GLD (243), 1990-01—2026-08,
440 common USD months; original stored observations, no generated market data.
Bootstrap: 1,000 iterations **for each** GMV and Max Sharpe, L=12, seed=42.
Resampled Frontier: 250 samples × 51 shared risk-aversion points.
All seven replays matched saved numerical results at abs=1e-7 / rel=1e-6.
Job elapsed times included worker startup. Generated per-job output was removed
during cleanup; future runs write it to the ignored Playwright test-results directory.

The five Playwright scenarios cover the live asset editor, offline Demo, all four real Live jobs,
explicit API validation failure, and displayed numerical/provenance differences
using a separately labelled mock API. Browser page errors are checked in Demo/Live.
The mocked difference case changes a saved allocation and matrix identifier
solely to verify warnings; it is not a market-data demonstration.

Validation: 207 pytest passed (one opt-in network test deselected), 15 Vitest passed,
5 Playwright passed, Ruff clean, TypeScript/Vite build successful,
Prettier check clean.
Read-only demo regeneration confirmed the existing database SHA-256 unchanged;
see `../analytics-demo/summary.json`.

## 2026-10-09 one-sided CV upgrade

- Desktop and mobile CV views showed one-sided periods,
  before/after directions, four fold allocations plus Ensemble, and validation
  coverage. Both screenshots were visually inspected; the 390×844 mobile chart
  was checked after responsive resizing, with no document horizontal overflow.
- All 7 Playwright scenarios passed, including actual stored-history jobs,
  comparison of four covariance estimators and exact replay of 7 saved results.
  The 15 completed UI-observed jobs shared one return-matrix
  fingerprint. Isolated API/Vite ports 8001/5174 were used for this verification.
- 272 backend tests passed, plus the separately enabled network integration test;
  23 Vitest tests passed. TypeScript/Vite, Ruff, Prettier and whitespace checks passed.
- Full method, file inventory and exact commands:
  [one-sided CV upgrade verification](../one-sided-cv-upgrade.md).
