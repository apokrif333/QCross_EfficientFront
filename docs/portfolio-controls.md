# Portfolio controls

Requests default to `portfolio_controls.mode=none`. Enabled controls require an
explicit original portfolio and a separate 100% hedge. The original optimizer
universe remains limited to 15 instruments. Hedge instruments are aligned once,
before Nominal/Real conversion, and history shortening is reported. The special
hedge ID `BIL_REFERENCE` uses the validated historical monthly USD BIL series.

Downside mode uses `downside_vol.target_annual` and `lookback_months` (3–24).
The unconditional downside RMS includes zero for positive base returns and is
annualized by sqrt(12). It uses the original fixed targets exclusively. Warm-up
keeps original exposure; zero downside gives alpha=1. Scaling is an exposure
rule, not a realized-risk guarantee. Signals observed at month end affect only
the following month. Changed targets trigger execution; unchanged signals do
not force monthly rebalancing. Scheduled policies use the current targets.
All asset trades are aggregated before the existing self-financing fee solver.

Results retain original metrics. `portfolio_controls` contains baseline and
controlled paths, costs, weights and signals. CV adds separate per-validation
comparisons, with signals restarted within each fold. Bootstrap uses the exact
existing sampled indices and returns all comparisons as
`portfolio_control_samples`. These do not alter trained allocations, risk
budgets or paired-comparison definitions. Reference fingerprints include the
joint original/hedge matrix and configuration. Legacy requests remain mode none.

## SMA / Momentum

`mode=trend` requires `trend.destination` (`remaining` or `hedge`) and per-original
asset `trend.asset_rules`: indicator `disabled`, `sma` or `momentum`, and integer
`lookback_months` 2–36. Missing rules are disabled. During warm-up assets remain
active. SMA uses the last L completed month-end TRI values including the current
one; equality is active. Momentum compares current TRI against L months earlier,
with the initial index at t=0. Zero momentum is active. Log TRI calculations
avoid cumulative-index overflow. A permanently zero TRI is inactive once ready.

Remaining mode normalizes active original target weights proportionally. Hedge
mode retains each active original target and routes the excluded target mass
to the common hedge. With no active original positions, either mode uses the
specified hedge. Reentry restores permitted target allocations. An asset may be
excluded from the original sleeve and still held through the hedge. Original
and hedge assets are combined by instrument ID; `BIL_REFERENCE` is resolved to
the actual BIL instrument ID before aggregation, including overlapping aliases.

The control signal is a month-end target change, not an intramonth stop. It
does not guarantee a volatility cap or future improvement. Execution occurs
once on the aggregate target, with the existing annual/calendar/threshold rule
applied to the permitted allocation. No terminal trade is made without a
following observation. All 21 current rebalancing rules remain available;
their API strategy records also include separate control comparisons.

## Requests and output

```json
{
  "instrument_ids": [1, 175, 243],
  "user_weights": {"1": 0.6, "175": 0.2, "243": 0.2},
  "return_basis": "real",
  "risk_free_source": "historical_bil",
  "transaction_fee_rate": 0.001,
  "portfolio_controls": {
    "mode": "trend",
    "hedge_weights": {"BIL_REFERENCE": 1.0},
    "trend": {
      "destination": "hedge",
      "asset_rules": {"1": {"indicator": "momentum", "lookback_months": 12}},
      "sensitivity_asset": "1"
    }
  }
}
```

Use this with the existing Frontier, CV, Bootstrap or Rebalancing Optimization
job endpoints. Preview also performs hedge alignment and reports shortening.
For downside mode replace the control object with:

```json
{
  "mode": "downside_vol",
  "hedge_weights": {"BIL_REFERENCE": 1.0},
  "downside_vol": {"target_annual": 0.09, "lookback_months": 12}
}
```

The compact editor is next to original weights. It has exactly three modes and
a common searchable hedge editor. Results show both gross/net paths, statistics,
actual combined holdings, desired original/hedge fractions and monthly signals.
CV tables and Bootstrap distribution summaries keep the controlled comparison
separate from the original trained portfolio comparison. Trade logs disclose
entries, exits, policy events and aggregate executed asset notionals.

Selecting one enabled original asset for sensitivity produces exactly 35
recalculations (lookbacks 2–36) with all other rules/fees/weights unchanged.
Run the analytics request again after changing that asset selection. The 3D
plot connects actual results by period; gross/net switching uses these stored
calculations. No interpolated surface or best-period selection is performed.
The selected history is shared by every scenario; synthetic samples retain
the full selected observation count. Signal warm-up restarts within each CV
validation block or Bootstrap sample, without fitting on future months.

## Verification

The remote main inspected before implementation was
`d316e451c4b47fae292e58def7b6f25bbcdd6ecc`. The first stage was committed as
`938a707` after targeted downside/API/simulator tests and UI tests/build passed.
The second commit adds trend routing, sensitivity and further integration checks.

Final verification commands:

```powershell
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --tb=short
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m ruff format --check app tests
# ui working directory:
npm.cmd test
npm.cmd run build
npm.cmd run format:check
$env:PLAYWRIGHT_CHANNEL='chrome'; npm.cmd exec playwright test
```

The controls-specific backend tests cover exact scaling, warm-up, zero downside,
SMA/Momentum timing, routing, reentry, overlapping net trades, original/BIL ID
deduplication, external hedge alignment and shortening, Real and Nominal API
jobs, all four covariance methods, fingerprints and unchanged Bootstrap outcomes.
UI tests cover exclusive controls, hedge validation and actual sensitivity data.
Browser tests import an actual synthetic simulator snapshot and render its 35
WebGL sensitivity points. No live market observations or production DB were
modified. Five Live browser scenarios require a connected backend and are skipped.

A local synthetic 240-month / three-asset benchmark with one numerical thread,
0.1% commissions and Momentum control completed 500 Bootstrap datasets in
18.070 seconds, including 250 paired comparisons, 500 controlled comparisons
without failures and 35 full-history sensitivity points. This is a local
measurement, not a production runtime guarantee; worker limits are unchanged.

Observed final checks: backend **468 passed**, 1 symlink-permission skip and 1
opt-in network test deselected (243.14 seconds). An additional controls and
Resampled Frontier provenance run passed all 29 tests. Frontend **53 passed**;
TypeScript/Vite build and Prettier passed. Full Chrome suite: **12 passed**, 5
Live scenarios skipped. Ruff lint passed and 122 Python files passed formatting.
Existing upstream deprecation/solver-accuracy and Plotly bundle-size warnings
remain; no failing final checks or production deployment is claimed.

## Changed files across both commits

```text
README.md
app/analytics/bootstrap.py
app/analytics/control_loader.py
app/analytics/models.py
app/analytics/portfolio_controls.py
app/analytics/portfolio_simulator.py
app/analytics/provenance.py
app/analytics/rebalancing.py
app/api/routes/frontier.py
app/schemas/frontier.py
app/schemas/portfolio_controls.py
docs/analytics-api.md
docs/analytics.md
docs/portfolio-controls.md
docs/rebalancing-engine.md
docs/rebalancing-optimization.md
tests/analytics/test_portfolio_controls.py
ui/e2e/rebalancing.spec.ts
ui/src/App.tsx
ui/src/components/ControlResults.tsx
ui/src/components/PortfolioControls.tsx
ui/src/lib/validation.ts
ui/src/test/portfolioControls.test.tsx
ui/src/test/rebalancingFixture.ts
ui/src/types.ts
```
