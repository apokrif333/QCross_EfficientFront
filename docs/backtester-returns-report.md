# Backtester monthly returns — Task 2.1

Verified against the live site on 2026-10-01. Final coverage and verification are recorded below.

## Actual retrieval mechanism

1. GET <https://www.lazyportfolioetf.com/portfolio-backtest-and-simulation/>.
2. Submit the ordinary HTML form using POST to that same URL. Maintain its
   session and fresh form fields. The publicly served `backtest-alpine.js`
   serializes `bktState`; `myLayout.js` supplies the form-integrity checksum.
3. Select an exact offered symbol and currency, 100% in the first portfolio,
   initial capital 1, contributions 0, filters NONE, no taxes and no extra hedge.
   Use the published earliest/latest date controls.
4. The result contains `const _d`; read the single portfolio's
   `rend.sliceArray.MAX.rendList` and nominal `capitalBase`.
5. Monthly Returns and Seasonality `downloadCsv()` in `components.js` creates
   a browser Blob from that same array. Its CSV is `Period;Return` with ascending
   `YYYY-MM` dates and percentage returns. There is no separate download URL.
   The adapter preserves the numeric observations and recreates that CSV before
   converting percentages to decimal returns for database storage.

Relevant currently published scripts:

- <https://www.lazyportfolioetf.com/wp-content/themes/acgpt-child/js/backtest-alpine.js?ver=4.4.2>
- <https://www.lazyportfolioetf.com/wp-content/themes/acgpt-child/js/components.js?ver=4.4.2>
- <https://www.lazyportfolioetf.com/wp-content/themes/acgpt-child/js/myLayout.js?ver=4.4.2>

The result also contains the next form. Reusing it in the same session matches
the normal edit-and-run browser workflow and removes unnecessary repeated GETs.
Requests remain sequential with a configurable pause after responses; transient
failures respect Retry-After. Explicit access denial stops the route. No denied
REST routes, URL crawling, browser automation or alternate access route is used.

The inflation-country selector chooses an additional real-capital comparison.
Extraction explicitly excludes `capitalNoInfl` and uses nominal `capitalBase`.
Both methods confirm dividend reinvestment without dividend taxation, where applicable.

## Pilots and precision

| Pair | Backtester history | Count | Comparison |
|---|---|---:|---|
| VTI–USD | 1793-01-31–2026-09-30 | 2,805 | Matches all months on fresh individual page |
| VTV–USD | 1927-01-31–2026-09-30 | 1,197 | Matches all stored individual-page months |

Both methods expose percentages rounded to at most four decimal places, rather
than the table's two-decimal display. Decimal parsing preserves the source precision.
Compounded growth, annualized return, volatility, drawdown, latest month, yearly
returns, contiguous month-end dates and observation counts passed precision-aware
validation. This verifies internal consistency, not independent market-data accuracy.

The older VTI database snapshot ends August 2026. Its June value is `-0.003853`;
the current individual page and backtester both report `-0.003854`. This source
revision is documented in `backtester_pilot_validation.json`; no existing VTI
observations were overwritten during comparison or skipped-series collection.
The original CSV export from the new VTI pilot has all 2,805 months.

Proxy transitions remain unknown beyond explicitly published broad cutoffs.
Converted histories are separate series with their actual denomination and
`currency_kind=converted`. Their shorter FX coverage is retained as published.
The site describes exchange-rate conversion and interest-rate differentials for
hedging; no extra hedge is selected. The exact FX provider and fixing conventions
are not disclosed and are recorded as unknown.

Published [terms](https://www.lazyportfolioetf.com/terms-and-privacy-policy/)
contain privacy/cookie information and an All rights reserved notice, with no
explicit licence to redistribute the dataset. The implementation performs local
HTTP ingestion and does not publish these data to qcross.org.

## Storage, statuses and outputs

The database remains `data/qcross.db`. A SQLite backup was made under the ignored
`data/raw/database_backups/` before migration. Series identity is unchanged;
extraction method does not create duplicate series. Each pair commits atomically.

Migrations:

- `0004_backtester_provenance`: method, native/converted and validation fields;
  old URL-only unsupported/unresolved checkpoints become pending.
- `0005_return_import_status`: descriptive status capacity on PostgreSQL/SQLite.

Pair statuses: completed, pending, partial, failed, not_available_in_backtester,
restricted. Missing page URLs trigger the backtester. A failed refresh reports
its failure separately from any preserved validated series.

Current outputs (after storage cleanup):

- `data/reports/backtester_pilot_validation.json`: read-only cross-validation and source revision.
- One-off pilot CSV/Parquet copies were removed during pruning. Pilot numerical
  comparisons remain in the operational validation report; the stored VTI/VTV
  series remain in the database and combined exports.
- `data/reports/lazyportfolio_returns_coverage.csv` and `.json`: all catalog ticker–currency pairs.
- `data/reports/lazyportfolio_returns_unresolved.csv`: missing combinations and exact reasons.
- `lazyportfolio_instruments.csv`: native-currency download status beside each symbol.
- `lazyportfolio_monthly_returns.csv` and `.parquet`: all stored series in long format,
  including denomination, extraction method and currency kind.
- `lazyportfolio_monthly_returns_wide.parquet`: explicitly USD-only; missing cells
  remain null, with no zero fill or implicit conversion.

Source HTML referenced by stored return series is preserved in the verified
`data/archive/validated_sources.zip`. Obsolete investigation snapshots, original
CSV duplicates, history logs and old backup copies were removed. New optional
snapshots use readable instrument/currency
names and gzip compression under `data/snapshots/lazyportfolio/`; they are disabled
by default. See `docs/data-storage.md`. Sanitized VTI/VTV numerical fixtures and a
representative form are in `tests/fixtures/` without live session tokens.

## Updated components

```text
app/ingestion/lazyportfolio/
  backtester.py             # ordinary form session and original CSV adapter
  backtester_pilot.py       # read-only two-method comparison
  return_pairs.py           # eligibility, checkpoints, fallback, resume, coverage
  returns_parser.py         # existing individual-page guard plus shared numerical parser
  returns_models.py
  returns_service.py        # individual-page ingestion; known URLs only
  returns_validation.py
app/db/models/returns.py
app/repositories/returns.py
app/exports/returns.py
app/schemas/returns.py
app/cli/returns.py
alembic/versions/
  0004_backtester_provenance.py
  0005_return_import_status.py
tests/ingestion/lazyportfolio/test_backtester.py
tests/fixtures/lazyportfolio_backtester_{form,VTI,VTV}.html
```

## Final batch and verification

Completed 2026-10-01. All **263 catalog instruments / 503 eligible ticker?currency pairs** have validated stored series.

| Metric | Count |
|---|---:|
| Completed pairs | 503 |
| Individual-page series | 173 |
| Backtester series | 330 |
| New series collected in Task 2.1 | 360 |
| Total observations | 361,179 |
| Native series | 261 |
| Converted series | 242 |
| Missing pairs | 0 |
| Failed validation | 0 |
| Failed / restricted / partial / pending pairs | 0 |

| Currency | Eligible and completed pairs |
|---|---:|
| USD | 74 |
| CAD | 79 |
| EUR | 72 |
| GBP | 68 |
| JPY | 70 |
| AUD | 70 |
| CHF | 70 |

**BNDX--CAD and EMB--CAD** are source selector identifiers for forced CAD conversion.
The site?s current `formatState()` removes `--<selected currency>` from `data-etf` and marks it as a forced currency swap. The returned weights use BNDX/EMB, with `etfSwap=true`.
The adapter verifies that mapping from the actual selector and returned state, keeps the catalog identifiers unchanged, and records the underlying component separately. Each has 501 observations, January 1985 through September 2026. They are labelled converted despite the selector?s CAD base-currency attribute.

### Pilot numerical checks

| Pair | Metric | Calculated | Published | Check |
|---|---|---:|---:|---|
| VTI?USD | final_capital | 169371464 | 169370085.3782 | PASS |
| VTI?USD | annualized_return | 0.0844351715 | 0.084435 | PASS |
| VTI?USD | annualized_volatility | 0.1478026213 | 0.147803 | PASS |
| VTI?USD | maximum_drawdown | -0.8460240698 | -0.846024 | PASS |
| VTV?USD | final_capital | 15240.16014 | 15240.2113 | PASS |
| VTV?USD | annualized_return | 0.1013737774 | 0.101374 | PASS |
| VTV?USD | annualized_volatility | 0.1841395588 | 0.18414 | PASS |
| VTV?USD | maximum_drawdown | -0.8439542611 | -0.843954 | PASS |

Return, volatility and drawdown above are decimal fractions; capital starts at 1. All monthly capital points and calendar-year comparisons passed rounding-aware checks. Full tolerance bounds and latest-month comparisons are in the pilot JSON.

### Preservation and test results

- Original **131,435 observations** and all original 143 series were compared with the pre-migration backup: **0 changed or deleted**.
- All **503 series / 361,179 observations** passed final database checks for calendar continuity, counts, month-end dates, finite values and valid source-validation records.
- All 361,179 long CSV/Parquet rows match, including dates, returns, currency, method and currency kind.
- The instrument CSV has 263 rows, all `returns_downloaded=True` and `returns_status=completed`.
- Unresolved-combinations CSV contains its header and **zero data rows**.
- Regular pytest: **121 passed**, 1 network test deselected. One external Starlette/httpx TestClient deprecation warning.
- Ruff checks and formatting pass; Alembic reports no pending schema changes. SQLite migrations were applied; no live PostgreSQL server was used.
- Repeated collection of completed BNDX--CAD exited 0 without another simulation POST or duplicated observations. The final batch resume skipped the 501 completed pairs and retrieved only the remaining two.

Existing validated series were intentionally skipped. Some retained source snapshots end August rather than September 2026; the coverage CSV states each exact endpoint. The current complete VTI pilot is exported separately through September; its older DB series and the June revision remain preserved pending explicit refresh/acceptance.

Precision remains source-rounded (at most four percentage decimals). The exact FX provider/fixing convention and detailed historical proxy transitions remain unknown. No missing observations, zero fills, fabricated transitions or independent FX conversions were introduced.
