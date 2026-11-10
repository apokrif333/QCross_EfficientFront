# BIL, inflation and return basis

New analytics requests default to nominal USD returns and `historical_bil`.
The independently selected BIL benchmark does not consume an asset slot. An
explicitly invested BIL position does count toward the 15 selected assets.
The existing 120-month minimum remains in force.

## Source and persistence

The existing validated USD BIL monthly nominal total-return series is reused;
there is no duplicate nominal series. LazyPortfolioETF extends the short-term
Treasury proxy back to January 1793; this is not literal ETF trading history.
`rendList` supplies original nominal monthly observations. Rounded cumulative
capital is never used to reconstruct nominal BIL returns.

The inflation importer fetches the original public BIL HTML using the existing
LazyPortfolioETF client, reads `const _d`, and validates an exact single-BIL USD
portfolio with no cashflows, taxes, filters or mixes. The existing nominal parser
also checks the initial month, MAX range, latest slice and invested capital.
Both positive finite capital arrays must have `len(rendList)+1` observations.
For nominal capital N and real capital R, with the initial capital at index 0:

```
inflation[t] = N[t]*R[t-1]/(R[t]*N[t-1]) - 1
```

The calculation uses a 60-digit Decimal context and stores a decimal fraction
rounded once to 18 decimal places, matching the existing exact-return storage
boundary. PostgreSQL uses Numeric(38,18); SQLite uses decimal text. No annual
inflation subtraction, FX conversion or external CPI substitution is performed.

Migration `0006_macro_references` adds `reference_series`,
`reference_observations` and `reference_revisions`. The BIL reference points to
the existing `return_series` row; inflation observations record month, value,
status, source snapshot and update time. Versions hash stored observations and
statuses. A refresh inserts only new months and updates changed accepted months.
Detected published-history revisions remain unapplied until `--accept-revisions`.
Explicitly resolved provisional-to-published transitions are automatically applied;
other revisions remain gated. Preview and validation roll back their transactions.

## Refresh commands

Run with the project's configured Python executable, from the repository root:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.cli.macro preview-bil-inflation
.\.venv\Scripts\python.exe -m app.cli.macro import-bil-inflation
.\.venv\Scripts\python.exe -m app.cli.macro validate-bil-inflation
.\.venv\Scripts\python.exe -m app.cli.macro status
```

`qcross macro ...` is equivalent. `--html PATH` uses a saved original HTML snapshot
without network access. Live fetches save the HTML in the configured macro snapshot
directory. Only BIL is fetched; the 503-asset collection is not redownloaded.
Nominal overlap is checked against stored BIL with a tolerance of one unit of the
published percentage precision, converted to decimal fraction. A nominal mismatch
requires the existing nominal-return revision workflow first; the macro importer
does not overwrite BIL nominal returns. Missing BIL months produce an actionable
request error to refresh validated nominal BIL history.

## Publication status

Statuses are `historical_reconstruction` before 1913, `published`, and
`assumed_zero_unpublished`. Genuine published zeros retain `published` status.
Publication is never inferred from a small numerical value. The maintained
`UNPUBLISHED` map in `inflation.py` documents 2025-10 and 2026-10 from the
investigated snapshots. Unknown internal gaps cause an error, never silent filling.
When publication is confirmed, remove the maintained exception or pass an explicit
`--published-month 2026-10` on refresh. This recomputes the rate from the capital
arrays, replaces the provisional observation, and changes the version fingerprint.
Normal refresh continues to honor unresolved exceptions until this confirmation.

API metadata lists assumed-zero dates and their count. No prominent UI warning is
shown. The small inflation information icon explains the source and zero policy.

Independent pinned BLS CPI observations are used only in offline tests. The test
checks 1913/1947 NSA and 1948/2019 SA pairs against the original BIL source to
0.00005 percentage points. It currently verifies 45 pairs, not the earlier
user-reported 1,283-pair study. The 2020+ vintage/revision differences are not
corrected and are not claimed to match modern official CPI exactly. Before 1913,
the source is a historical reconstruction, with its associated limitations.

## Complete calculation chain

The database loader aligns asset observations, BIL, and (in Real mode) inflation
once before starting a job. For each month and every asset, including BIL:

```
real = (1 + nominal) / (1 + inflation) - 1
```

The complete transformed monthly matrix drives expected returns, covariance,
correlations, volatility, CAGR, drawdown, constraints, GMV, maximum return, target
return, frontier, equal-weight, user and asset-only references, CV and bootstrap.
An 8% target in Real mode means 8% annual arithmetic real return.
Nominal and Real retain identical months when inflation is present or a specifically
identified unpublished-zero exception applies. Inflation alone never truncates
the requested sample. Unknown inflation gaps are rejected explicitly.

For fixed annual mode, nominal monthly risk-free return is
`(1+annual_rate)^(1/12)-1`. Real fixed mode deflates this monthly nominal value
with the same inflation observations. Legacy fixed nominal estimates retain the
old `(annual_expected_return - annual_fixed_rate)/estimated_asset_volatility`
objective for numerical compatibility. Rates must exceed -100%.

## Two Sharpe ratios and the optimizer

Historical Sharpe uses observed monthly differential portfolio returns:
`sqrt(12)*mean(asset_returns @ weights - risk_free)/std(..., ddof=1)`.
Zero differential volatility returns null.

For historical BIL and real fixed mode, `sharpe_ratio` is the estimated objective:
`weights @ annual_mean(D) / sqrt(weights @ annual_covariance(D) @ weights)`,
where `D = asset_returns - risk_free[:,None]`. Its covariance uses the selected
estimator, independently of asset covariance and with its own `cv_linear`
hyperparameters. The separate `historical_sharpe_ratio` uses observed differences.
Means and covariances are annualized by 12. Diagnostics and covariance parameters
for ordinary asset risk and differential risk are exposed separately.

The existing constrained homogeneous Max Sharpe solver receives the differential
moments through optional arguments. Its nonpositive-excess vertex enumeration,
group constraints and failure handling are retained. GMV and frontier continue
to use asset moments. The BIL Max Sharpe point is labeled as such and plotted at
its actual asset expected return and asset volatility; it is not projected onto
the classical frontier. Differential covariance need not have the same geometry
as asset covariance. A selected BIL asset has an exact zero differential column;
the same estimator operates on nonzero columns, then zero rows/columns are
reinserted. One-dimensional estimators use the same formulas without a fallback.

## CV and Stationary Bootstrap

`splitting.py` is unchanged: even K=2–20, default 4; portfolio 120/60 and inner
covariance 60/30 minima; no embargo or shuffle. Each fold estimates both asset and
differential moments exclusively from its one-sided training months. Inner
`cv_linear` also sees only those months. Validation historical Sharpe uses its own
matching BIL observations, not a full-period average. The ensemble remains the
equal arithmetic mean of all fold weights; failed folds prevent a partial ensemble.

Stationary sampling is unchanged. Assets and BIL use exactly the same sampled
indices. Real asset and BIL returns are deflated before sampling. Differential
covariance is re-estimated per Max Sharpe sample. Conditional `cv_linear` holds
the full-sample asset and differential shrinkage selections fixed independently.
Shared-risk-aversion resampled-frontier averaging remains unchanged; averaged
weights are evaluated against the original selected-basis panel. Bootstrap
weight intervals are allocation sensitivity, not guaranteed future returns.

## API, snapshots and fingerprints

All five analytics POST endpoints accept `return_basis` and `risk_free_source`:

```json
{"instrument_ids":[1,175,243],"return_basis":"real","risk_free_source":"historical_bil","covariance_method":"nonlinear","objective":"max_sharpe","cv_folds":4}
```

```json
{"instrument_ids":[1,175,243],"return_basis":"nominal","risk_free_source":"fixed_annual","risk_free_rate":0.03}
```

Responses expose basis/source, `risk_free_mean_return` (annual arithmetic mean
in the chosen basis), `reference_metadata`, differential moments/diagnostics and
both Sharpes. Reference metadata includes BIL series ID/version, inflation
version, effective dates/count, historical reconstruction dates, and provisional
dates/count. Source snapshot IDs are persisted with the observations.

New requests without either rate field use BIL. Legacy requests containing
`risk_free_rate` but no `risk_free_source` explicitly retain fixed-rate semantics.
Old v1 snapshots remain readable; absence of the new fields means nominal fixed
annual, including in replay. Loading an old Demo does not relabel it as BIL.
New Live requests default to historical BIL. Overlay compatibility requires
matching basis/source/settings, asset matrix and reference-panel version hash.

Engine version 2.0.0 includes `references.py` in the source digest. The reference
panel fingerprint includes the selected matrix, BIL vector, inflation values and
statuses, basis and reference versions. The calculation fingerprint additionally
includes effective settings, both covariance parameter sets, engine identity and
bootstrap sample fingerprint. Inflation revisions affect Real results, while
nominal asset observations remain untouched. BIL revisions change BIL results.

## Verified local data

Original BIL HTML was fetched on 2026-11-10. Inflation coverage is 1793-01 through
2026-10: 2,806 months; 1,440 reconstructed, 1,364 published and 2 provisional.
The referenced existing nominal BIL series is ID 59, 1793-01 through 2026-09,
2,805 observations. October nominal BIL can be added using the established
nominal ingestion workflow; it was not fabricated or copied from capital growth.

`docs/macro-import-verification.json` records the completed local SQLite import.
All 503 nominal series and 361,179 nominal observations retained their original
content hash `207810e3293e35c79a00d598d4726dd5759edf2e35ad9e1598d6bc02fee16080`.
SQLite migration upgrade/downgrade was tested on a database copy. PostgreSQL
migration SQL generation was verified; no live PostgreSQL server or Railway
deployment was performed. Existing demo artifacts remain explicitly legacy
nominal/fixed calculations rather than invented BIL demo results.
