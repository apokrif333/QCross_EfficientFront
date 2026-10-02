# Portfolio Lab browser verification

Verified locally in Chromium on 2026-10-02; no public deployment.

- `demo-desktop.png`: desktop Frontier, real stored USD results.
- `demo-mobile.png`: 390×844 viewport, collapsed settings, resized Plotly chart;
  document has no horizontal overflow; wide tables scroll inside their container.
- `live-compare.png`: four actual API covariance methods on one graph.
- `live-jobs.json`: 15 completed jobs observed through UI requests/status polling/result retrieval,
  including all four analytics endpoints, four-method comparison, and seven snapshot replays.

Live observations: VTI (1), TLT (175), GLD (243), 1990-01—2026-08,
440 common USD months; original stored observations, no generated market data.
Bootstrap: 1,000 iterations **for each** GMV and Max Sharpe, L=12, seed=42.
Resampled Frontier: 250 samples × 51 shared risk-aversion points.
All seven replays matched saved numerical results at abs=1e-7 / rel=1e-6.
Job elapsed times include worker startup; exact values are in `live-jobs.json`.
The first four jobs took approximately 1.45 s, 1.34 s, 9.36 s, 8.39 s respectively.

The four Playwright scenarios cover offline Demo, all four real Live jobs,
explicit API validation failure, and displayed numerical/provenance differences
using a separately labelled mock API. Browser page errors are checked in Demo/Live.
The mocked difference case changes a saved allocation and matrix identifier
solely to verify warnings; it is not a market-data demonstration.

Validation: 194 pytest passed (one opt-in network test deselected), 12 Vitest passed,
4 Playwright passed, Ruff clean, TypeScript/Vite build successful,
Prettier check clean, npm audit zero vulnerabilities.
Read-only demo regeneration confirmed the existing database SHA-256 unchanged;
see `../analytics-demo/summary.json`.
