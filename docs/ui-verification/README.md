# Portfolio Lab browser verification

Latest macro upgrade (2026-11-10): 9 Playwright scenarios passed, followed by 2
targeted final checks of Real/BIL results and legacy defaults. Actual local
Real/BIL results are in `macro-real-bil-desktop.png` and
`macro-real-bil-mobile.png`, both visually inspected. Settings-only snapshots
`macro-settings-desktop.png` and `macro-settings-mobile.png` use a mocked preview.
See [exact verification commands and evidence](../macro-verification.md).

Verified locally in Chromium on 2026-10-06; no public deployment.

- `demo-desktop.png`: desktop Frontier, real stored USD results.
- `demo-mobile.png`: 390×844 viewport, collapsed settings, resized Plotly chart;
  document has no horizontal overflow; wide tables scroll inside their container.
- `live-compare.png`: four actual API covariance methods on one graph.
- `live-jobs.json`: 15 completed jobs observed through UI requests/status polling/result retrieval,
  including all four analytics endpoints, four-method comparison, and seven snapshot replays.
- `asset-builder-live-desktop.png` / `asset-builder-live-mobile.png`: new top asset
  editor, groups 1/3/5, a 100% VTI reference, both return definitions and explicit
  constraint violation. Real stored 1793-01—2026-08 history, 2,804 months.

Live observations: VTI (1), TLT (175), GLD (243), 1990-01—2026-08,
440 common USD months; original stored observations, no generated market data.
Bootstrap: 1,000 iterations **for each** GMV and Max Sharpe, L=12, seed=42.
Resampled Frontier: 250 samples × 51 shared risk-aversion points.
All seven replays matched saved numerical results at abs=1e-7 / rel=1e-6.
Job elapsed times include worker startup; exact values are in `live-jobs.json`.
Exact execution times for the current run are retained in that file.

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

- `one-sided-cv-desktop.png` and `one-sided-cv-mobile.png`: one-sided periods,
  before/after directions, four fold allocations plus Ensemble, and validation
  coverage. Both screenshots were visually inspected; the 390×844 mobile chart
  was checked after responsive resizing, with no document horizontal overflow.
- All 7 Playwright scenarios passed, including actual stored-history jobs,
  comparison of four covariance estimators and exact replay of 7 saved results.
  `live-jobs.json` contains 15 completed UI-observed jobs sharing one return-matrix
  fingerprint. Isolated API/Vite ports 8001/5174 were used for this verification.
- 272 backend tests passed, plus the separately enabled network integration test;
  23 Vitest tests passed. TypeScript/Vite, Ruff, Prettier and whitespace checks passed.
- Full method, file inventory and exact commands:
  [one-sided CV upgrade verification](../one-sided-cv-upgrade.md).
