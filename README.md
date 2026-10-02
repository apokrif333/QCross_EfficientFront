# QCross instruments and historical returns backend

The first backend milestone for future qcross.org tools: discover the complete
LazyPortfolioETF simulator instrument universe, validate it, normalize it, and
make it available through SQL storage, a CLI, and a read-only API. The second
milestone adds validated historical monthly total returns in native currencies.
Milestone 2.1 completes missing catalog/currency pairs through the backtester,
with separate provenance for currency-converted histories.

Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, httpx,
BeautifulSoup/lxml, PostgreSQL (production) and SQLite (development/tests).
The current local PyCharm interpreter is Python 3.14.6.
Parquet exports use PyArrow.

## Setup

Run from the project root. On Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m alembic upgrade head
```

Any Python version >=3.12 is supported; if a virtual environment is already
configured in PyCharm, use that interpreter instead of creating another one.
On macOS/Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python -m alembic upgrade head
```

The default database is `sqlite:///./data/qcross.db`. For PostgreSQL, set:

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@host:5432/qcross
```

Railway-style `postgres://` and `postgresql://` URLs are also normalized to the
psycopg v3 driver. Apply Alembic migrations to each database before running
discovery or instrument API queries. Schema creation is explicit rather than
an API startup side effect. Keep actual credentials in environment variables
or an ignored `.env` file.

## Discover and export

```bash
qcross lazyportfolio discover --save-raw --export
# Equivalent without installing the entry point:
python -m app.cli.lazyportfolio discover --save-raw --export
```

Options:

- `--save-raw` / `--no-save-raw`: override `SAVE_RAW_SNAPSHOTS`.
- `--export`: write the CSV, JSON, and a full discovery diagnostic report.
- `--no-db`: parse and validate without opening a database; only current-page
  completeness checks run, because no stored historical baseline is read.
- `--verbose`: enable debug logging.

Outputs are UTF-8:

```text
data/snapshots/lazyportfolio/catalog/instrument_catalog_selector_<UTC timestamp>.html.gz
data/exports/lazyportfolio_instruments.csv
data/exports/lazyportfolio_instruments.json
data/reports/lazyportfolio_discovery_report.json
```

CSV has `source_symbol,name,category,currency,available_currencies,is_active`.
The `available_currencies` CSV field uses `|` between codes; JSON uses an array
(null when the source does not provide availability). JSON includes normalized
metadata and original option/group attributes in `raw_metadata`. These exports
describe the current successful discovery, so all exported rows are active;
inactive historical rows remain available through the database/API. JSON uses
`discovered_at`, rather than fabricating database `first_seen_at` values.

Saved snapshots preserve the original response bytes. They are optionally saved
after a successful HTTP fetch, including responses that later fail parser
validation. Failed validation never writes instrument exports or changes the
database. Database synchronization commits before exports are written; an export
filesystem error therefore leaves a valid database import intact. Each export
file is replaced atomically, but the export and report files are not a single
transaction. Generated snapshots, exports, and the local database are ignored
by Git.

## Current extraction mechanism

Inspected on 2026-10-01:

Source: <https://www.lazyportfolioetf.com/portfolio-backtest-and-simulation/>

The HTTP response contains 12 server-rendered `select.asset-dropdown` elements,
one per asset row. Each contains an empty placeholder and the full universe
under `<optgroup label="...">`. The option `value` is the exact simulator
identifier, its text is the name, and its optgroup label is the source category.

The page's `window.bktInitialState` is the simulator's current selection/settings,
not a complete instrument catalogue. Its
`wp-content/themes/acgpt-child/js/backtest-alpine.js?ver=4.4.2` initializes
Select2 on the existing `.asset-dropdown` elements, reading `data-basecurrency`,
`data-benchmarkcurrency`, and `data-hedgedversion` from selected options. That
initialization has no AJAX catalogue loader. Consequently, one HTTP page fetch
is sufficient; the production client does not fetch JavaScript or run a browser.

The parser reads every dropdown and verifies that their symbol sets agree.
It removes exact repeated records and retains conflicting variants in diagnostics
instead of choosing a winner. A separate server-rendered available-assets panel
(`.asset-block .asset-li-list .mini-badge`) provides a completeness cross-check:
every panel symbol must be present in the selectors.

Live discovery found **263 unique instruments in 10 categories**, with 3,156 raw
option records and 2,893 identical repetitions removed. The panel lists 261
symbols. `BNDX--CAD` and `EMB--CAD` occur in the selectors but not the panel;
these source identifiers are retained, and the discrepancy is reported as a
warning. This is a source observation, not a hard-coded parser exception.
Sentinel tickers and fixture expectations are regression checks; the universe
itself is always discovered from source markup.

| Source category | Instruments |
| --- | ---: |
| US Stocks | 30 |
| Global / ex-US Stocks | 48 |
| US Theme - Sectors | 48 |
| US Factor - Dividends - Misc | 23 |
| US Fixed Income | 48 |
| EU Fixed Income | 12 |
| CA Fixed Income | 11 |
| UK Fixed Income | 7 |
| International Fixed Income | 15 |
| Commodity | 21 |

Bitcoin and Managed Futures currently belong to the site's `Commodity` group;
real estate instruments are grouped under the site's stock groups. The examples
in the original requirements are not imposed as a taxonomy.

`source_symbol` and initial `ticker` remain verbatim, including `^BTC`, `VUN.TO`,
`SXR8.DE`, `CSP1.L`, and synthetic identifiers containing `--`. Only name/category
whitespace is normalized. `currency` is taken directly from `data-basecurrency`;
`data-currency` describes simulator currency availability and stays in raw
metadata as well as the explicit `available_currencies` field. Country, exchange,
asset type and subcategory remain null because the
source does not explicitly provide them. No taxonomy or metadata is inferred
from ticker suffixes, color classes, or instrument names.

### Instrument currency and simulator currency

The site's Currency control selects a simulation/reporting currency. The
JavaScript `bktRefreshAssetList(currency)` uses `data-currency` to show applicable
instruments, and marks instruments whose `data-basecurrency` differs from the
selected currency as currency conversions. Consequently, the two fields describe
different properties:

- `currency`: the instrument currency supplied by `data-basecurrency`.
- `available_currencies`: the currencies for which the source offers this instrument,
  supplied by its option's `data-currency`. This is source availability, not a
  list of trading currencies or an internal QCross classification.

For example, these records share a source label but have distinct identifiers:

| Source symbol | Name | Currency | Available currencies |
| --- | --- | --- | --- |
| VTI | US Total Stock Market | USD | USD, JPY, AUD, CHF |
| VUN.TO | US Total Stock Market | CAD | CAD |
| XD9U.DE | US Total Stock Market | EUR | EUR |
| XDUS.L | US Total Stock Market | GBP | GBP |

The current universe has 74 USD, 49 CAD, 72 EUR and 68 GBP instruments.
Simulation availability is USD 74, CAD 79, EUR 72, GBP 68, JPY 70, AUD 70, CHF 70.
For example, VTV is a USD instrument also offered in CAD simulations.
Source identifiers remain the logical key. Equal names, even within one currency,
do not imply duplicate records: DBC and GSG are two distinct USD selector entries
both labeled `Broad Commodities`. Both are retained.

Apply the new migration and run discovery to populate currency availability on
existing rows:

```bash
python -m alembic upgrade head
qcross lazyportfolio discover --export
```

The sanitizer preserves both currency attributes, including intentional empty
slots in `data-currency`, so the existing fixture supports currency regression tests.

## Completeness and synchronization

Validation rejects missing selectors/panel, incomplete documents, unbalanced
markup, inconsistent selector universes, missing identifiers, value/data-etf
mismatches, conflicting duplicate metadata, and panel symbols missing from the
selectors. It requires at least 51 instruments and the sanity-check symbols
`VTI`, `SPY`, `TLT`, `GLD`. Empty names and unclassified categories are reported.
There is no text-scraping fallback that could silently accept changed markup.

`ingestion_states` stores successful source counts, category counts, the total
historical high-water count, and the last success timestamp. Each later database
run must retain at least 80% of that total high-water count and 80% of each
previous source category, rounded up. The category guard catches an entire
small group disappearing even when the total barely changes. The total high-water
mark prevents repeated imports from gradually lowering the count baseline.
The maximum accepted drop fraction is configurable in `.env.example`; genuine
large source removals require explicit investigation and review of this threshold.

Synchronization uses the unique `(source, source_symbol)` key. It preserves IDs,
first-seen and creation timestamps; refreshes last-seen and updated timestamps;
updates changed metadata; and reactivates rediscovered rows. Identical repeat
imports count as `unchanged` while refreshing observation timestamps. Missing
rows become inactive only after validation; last-seen remains their last actual
sighting. Other sources are unaffected.

Validation against the historical baseline and all database changes run in one
transaction. SQLite uses `BEGIN IMMEDIATE`; PostgreSQL uses a per-source advisory
transaction lock so concurrent runs cannot race the baseline/deactivation step.
An older response cannot replace a newer successful discovery. Failed validation
or a write error rolls back the import and baseline together.

## API

```bash
python -m uvicorn app.main:app --reload
```

- `GET /health` returns `{"status":"ok"}`.
- `GET /api/v1/instruments` returns instrument objects, with optional `source`,
  `category`, `active`, and case-insensitive `search` on symbol/name.
- `currency=USD` filters the instrument's own currency.
- `available_currency=USD` filters source simulation availability, matching the
  site's Currency control. Currency filters accept three letters, case-insensitively,
  and combine with all other filters before pagination.
- `GET /api/v1/instruments/{id}` returns one instrument or HTTP 404.
- `limit` defaults to 1000 (maximum 1000); `offset` defaults to 0.
- Interactive documentation: <http://127.0.0.1:8000/docs>.

```bash
curl 'http://127.0.0.1:8000/api/v1/instruments?source=lazyportfolioetf&active=true'
curl 'http://127.0.0.1:8000/api/v1/instruments?search=%5EBTC'
curl 'http://127.0.0.1:8000/api/v1/instruments?source=lazyportfolioetf&available_currency=GBP&active=true'
curl 'http://127.0.0.1:8000/api/v1/instruments?currency=USD&search=US%20Total%20Stock%20Market'
```

The application can later run on Railway with an environment-provided
`DATABASE_URL` and a start command such as
`uvicorn app.main:app --host 0.0.0.0 --port "$PORT"`. Deployment and scheduling
remain outside this milestone.

## Architecture

```text
External source
    ↓
source client (HTTP, retries, encoding)
    ↓
source parser (instruments + diagnostics)
    ↓
validation (structure, redundancy, sanity checks)
    ↓
normalization (source-neutral InstrumentRecord)
    ↓
repository (history validation + atomic upsert) / database
    ↓
API
```

The CLI calls `LazyPortfolioDiscoveryService`, which is independent of FastAPI.
It is also callable by tests or future scheduled/admin jobs. All source-specific
fetching, markup, diagnostics, normalization and completeness rules live in
`app/ingestion/lazyportfolio/`. Generic records/repository interfaces live in
`app/ingestion/base.py`; models and storage know nothing about LazyPortfolioETF.
Other source adapters can use the same generic record/repository contract.

Future source adapters and analytics modules can be added under `app/ingestion/`
and `app/analytics/` when needed. This milestone includes instrument discovery
only; return-series ingestion, optimization, bootstrap/Monte Carlo, ETF updates,
frontend integration, authentication, deployment and cron jobs are deferred.

## Tests and diagnostics

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m alembic check
```

Tests use the sanitized saved fixture and mocked HTTP, with no live-network
dependency. They cover symbol preservation, names/categories, normalization,
duplicates/conflicts, optional metadata, malformed/truncated pages, count and
category loss, retry/error/encoding behavior, atomic idempotent synchronization,
inactive/reactivated rows, source isolation, stale responses, exports, CLI exit
codes, API filters/details/pagination, and SQLite migration upgrade/check/downgrade.
Live discovery is run separately through the same production CLI/service/parser.
PostgreSQL execution requires a PostgreSQL instance; local database tests use SQLite.

The fixture's provenance file records capture time and SHA-256 hashes. To prepare
a reviewed replacement from a fresh raw snapshot:

```bash
python scripts/sanitize_lazyportfolio_fixture.py data/snapshots/lazyportfolio/catalog/<snapshot>.html.gz
```

The sanitizer retains the complete catalogue and two selector copies while
removing scripts, forms, ads, tokens and tracking. Review updated fixture/count
expectations before committing them. Do not automate fixture replacement in tests.

Clean standard logging includes `lazyportfolio.fetch.started`,
`lazyportfolio.fetch.completed`, `lazyportfolio.parse.completed`,
`lazyportfolio.validation.completed`, and `lazyportfolio.sync.completed`.
The client makes one request on a normal run, with separate connect/read timeouts,
bounded attempts and exponential delays for transport failures, HTTP 429 and 5xx.
`Retry-After` is respected; if the requested pause exceeds `HTTP_MAX_RETRY_DELAY`,
the run fails with an instruction to retry later instead of requesting again early.
HTML is not written to logs. The CLI and exported report include category counts,
duplicates/conflicts, missing identifiers, empty names, unclassified categories,
instrument currency counts, simulation availability counts, missing currency
metadata, warnings and validation errors.

## Historical monthly returns (milestone 2)

### Identified source mechanism

The public VTI page is
<https://www.lazyportfolioetf.com/etf/vanguard-total-stock-market-vti/>.
Its inline Alpine initialization contains a **JSON object**, `const _d = {...}`.
For the matching instrument (`pID`, also checked against a single 100% component):

```text
_d.settings.currency                         = USD
_d.portfolios.VTI_1.rend.sliceArray.MAX
    periodoStart = 1792-12                   # capital baseline, NOT an observation
    periodoMin   = 1793-01
    periodoMax   = 2026-08
    monthDiff    = 2804
    rendList     = [-0.4688, -5.2762, ...]    # percentages, up to 4 decimal places
    capitalBase  = [1, 0.9953, ...]           # nominal accumulated capital
_d.portfolios.VTI_1.kpiData.MAX.base           # published nominal statistics
```

`myLayout.js` implements `financials_dpGetMonthlyChart()` by reading `rendList`.
The capital-growth chart reads `capitalBase`. ALL DATA selects the already loaded
MAX slice; it does not download a longer series. The Monthly Returns Distribution
table calls `.toFixed(2)` for display, so scraping that table would lose precision.
We parse JSON with `Decimal`, divide percentages by 100, and never execute JavaScript.
`capitalNoInfl` is inflation-adjusted and is not imported. Live/daily data and
`jsAsyncLive` are excluded. The default page explicitly states dividend reinvestment
without dividend taxation when applicable. Cash flows, taxes, contributions,
rebalancing, portfolio combinations and trend filters are rejected by the parser.

Changing instruments/currency in the backtest form changes simulation inputs and
submits a form; it is not an instrument-return API. Its JavaScript also calls
`POST /wp-json/backtest/v1/load-site-portfolios` for a portfolio-selector catalogue,
not monthly observations. That endpoint returned HTTP 403 during investigation;
we did not retry it or attempt to bypass the restriction. It is not used by this module.
Individual-page extraction is retained for **already known** URLs. Site-wide URL
crawling, ranking searches, correlation expansion and sitemap traversal have been
removed from the ingestion workflow. The old `resolve-links` command is retired.
A missing page URL sends the exact instrument/currency pair to the backtester.

### Backtester adapter (milestone 2.1)

The current form uses `POST /portfolio-backtest-and-simulation/` with its normal
server-issued form fields, cookies and public FNV form-integrity checksum.
`backtest-alpine.js` serializes per-column options into `bktState`; scalar fields
include `aC` (currency), `a1` (symbol), `p1_1=100`, `iA=1`, `cA=0`, and `mF/yF/mT/yT`.
The adapter selects the form's earliest/latest dates, one asset, no hedge,
`ft1=NONE` and no taxes. Inflation country `cI` selects a separate comparison;
nominal observations come only from `rendList` and `capitalBase`, never
`capitalNoInfl`. Requested dates and non-secret parameters are stored in metadata.
No private monthly-return API is assumed or called.

The POST response embeds `const _d`; the sole simulated portfolio has
`components.weights={exact_symbol:100}`. The dedicated adapter checks currency,
initial capital, weights, cash flows, taxes, filters and the complete MAX slice.
The individual-page parser retains its original requirement for an ETF page.

In `components.js`, Monthly Returns and Seasonality `downloadCsv()` reads
`financials_dpGetMonthlyChart('MAX')`, increments months from `periodoStart`,
and creates a browser Blob with **`Period;Return`**, where Return is a percentage.
There is **no CSV download HTTP endpoint**. The adapter reproduces those columns
from the identical numerical array with Decimal arithmetic. These are chronological
monthly observations, not calendar-month seasonality averages. Display rounding
is two decimals; original percentages and CSV retain up to four decimals.

`backtester-pilot` compares VTI?USD and VTV?USD against existing stored series
without modifying observations. The October 1 pilot found VTI's June 2026 source
revision (-0.003853 to -0.003854) and a newly published September. A fresh **known**
VTI detail page exactly matched all 2,805 backtester months, confirming a source
revision rather than a method discrepancy. VTV matched all 1,197 stored months.
The older VTI database series remains unchanged until explicit revision acceptance.
Detailed comparison and precision-aware capital/KPI checks are in
`data/reports/backtester_pilot_validation.json`.

Candidates are reconciled with the live selector's actual `data-currency` values,
not a cross-product of all instruments and seven currencies. The current 263
symbols offer 503 eligible pairs. Converted series are stored independently and
labelled `currency_kind=converted`. The site describes FX conversion or interest
rate differentials for hedging; this adapter selects **no hedge** and records
exchange-rate conversion with the FX provider/fixing convention unknown. It does
not implement independent currency conversion or claim converted returns are native.

Series identity stays `(instrument_id, source, currency, frequency, return_type)`;
extraction method is provenance, not a duplicate-series key. Migrations
`0004_backtester_provenance` and `0005_return_import_status` retain observations,
add method/currency/validation fields and replace old URL-only unsupported statuses
with pending. SQLite and PostgreSQL support the full descriptive status names.

### Access and reuse conditions

Checked 2026-10-01: `/robots.txt` contains an empty `Disallow` rule. The published
[Terms and Privacy Policy](https://www.lazyportfolioetf.com/terms-and-privacy-policy/)
contains privacy/cookie information and an All rights reserved notice; it provides
no explicit data redistribution licence. Robots accessibility does not grant one.
This collector uses ordinary public HTTP pages for local ingestion, no browser
automation, login, proxy rotation or access-control bypass. Do not assume permission
to republish the source dataset on qcross.org from these access conditions.

Requests are sequential, with `RETURNS_REQUEST_INTERVAL=3` seconds **after** each
response. Connect/read timeouts and transient retries reuse the existing client.
429/5xx respect Retry-After; retries use at least the configured interval. A final
401/403/429 or excessive Retry-After stops the batch. A successful instrument is
committed independently; an unrelated instrument failure does not roll it back.

### Data storage, validation and reconstruction

Migration `0003_monthly_returns` adds `return_series`, `monthly_returns`,
`return_revisions` and persistent `return_imports` checkpoints. Apply it with
`python -m alembic upgrade head`. Series reference the existing instrument ID and
have a unique provider/currency/frequency/return-type identity. Observations are
unique by `(series_id, date)`. Dates are month ends; decimal returns are stored as
PostgreSQL NUMERIC(38,18), or exact decimal text on SQLite to avoid float conversion.
Neither raw snapshots nor exported data are committed automatically.

The VTI methodology publishes a reconstruction cutoff through **December 2001**.
Those observations are `historical_proxy`; later months are `reported_etf_era`
according to that broad statement. Individual proxy/index transitions remain
unknown. Without a published cutoff, provenance is `unknown`; we do not invent
inception dates. See the site's [Data Sources](https://www.lazyportfolioetf.com/data-sources/)
for its general reconstruction approach. Monthly source precision is separately
flagged, e.g. `rounded_4dp_percent|historical_proxy`.

Validation requires the full MAX calendar span and declared count, month ends,
strict order, no duplicates/gaps, completed months, finite values and returns >= -1.
Returns above 50% in magnitude are reported for review and retained. For the VTI
pilot, January 1793 is a required starting month. Compounded growth is compared at
**every month** against the source capital array. CAGR, population monthly volatility
scaled by sqrt(12), maximum drawdown, latest 1M return, and every calendar year's
return are checked. Partial first/last calendar years are marked with their month
count. Comparisons use half-unit source rounding bounds accumulated multiplicatively,
and the reported capital/KPI rounding resolution. Annual comparisons use the same
capital endpoints/formula as the published annual-return table. This validates
internal source consistency, not an independent market-data provider.

Discovery cannot shorten already imported history or apply stale snapshots.
`update` downloads the complete current public state to check both new months and
revisions, then inserts only missing observations. The source does not expose a
verified missing-month-only endpoint. Changed values are logged and recorded in
`return_revisions`; the series is left unchanged unless `--accept-revisions` is
explicitly supplied. No scheduled updates have been configured.

### Commands and exports

```bash
qcross returns discover --symbol VTI --save-raw
qcross returns validate --symbol VTI
qcross returns discover --all --save-raw
qcross returns backtester-pilot
qcross returns discover --symbol VTI --currency JPY --save-raw
qcross returns update --all
qcross returns update --symbol VTI --accept-revisions
qcross returns coverage
qcross returns export --symbol VTI --currency USD --format csv
qcross returns export --symbol VTI --currency USD --format parquet
qcross returns export --format parquet
qcross returns export --format parquet --wide --currency USD
```

Equivalent: `python -m app.cli.returns <command>`. Bulk backtester collection
requires both validated pilots. Repeating `discover --all` skips valid existing
series and resumes missing/failed pairs. `--symbol` defaults to the instrument's
native currency; `--currency` selects an explicit denomination, including with
`--all`. `update` requests an explicit refresh. Source revisions are recorded and
require `--accept-revisions`; mismatches never silently replace validated history.
For each instrument/currency pair, refresh first reuses the extraction method of
its stored **validated** series. Thus a pair previously imported by the backtester
does not retry an unused or broken known ETF URL on every update. Without a valid
previous method, a known individual page is preferred for the native currency;
other pairs use the backtester. Detail pages are never used for another currency.
An ordinary extraction or validation failure tries the other eligible method
once; a successful fallback becomes the preferred method for future refreshes.
Access restrictions stop the batch, and pending revisions do not trigger another
download. Database/file write failures do not trigger alternative downloads.
The existing `return_series.extraction_method` stores the successful method;
no migration or initial redownload is required. `return_imports.details.method_attempts`
records the latest run's methods, results, failure reasons and elapsed seconds
(including source waits, parsing and synchronization). Logs emit
`lazyportfolio.returns.method.selected`, `.fallback` and `.completed`.
This avoids unnecessary failed requests; it is not a measured fastest-route
benchmark and does not shorten the configured request interval.

`validate --symbol SYMBOL --currency CODE` checks the stored raw snapshot and
observations. `--live` checks the current known page or backtester without DB writes.

Snapshots are **disabled by default**. Use `--save-raw` or
`RETURNS_SAVE_RAW_SNAPSHOTS=true` when investigating source changes. They are
compressed `.html.gz` under `data/snapshots/lazyportfolio/`, named with exact-safe
symbol, currency, method and timestamp, e.g. `VTI_USD_backtester_<timestamp>.html.gz`.
Unsafe filename characters are encoded (`^BTC` becomes `~5EBTC`); source identity
in the database stays unchanged. Duplicate raw percentage CSVs are not produced.
The validator reads old snapshots directly from the verified archive, retaining
their original DB paths and SHA-256 hashes. Without a saved snapshot, use `--live`
for a new source comparison. Sanitized numerical
pilot fixtures and a form fixture retain no live session tokens or nonces.

Coverage refreshes `data/reports/lazyportfolio_returns_coverage.json` and `.csv`.
The full CSV has one row per ticker?currency pair: eligibility, status, method,
native/converted, dates, count, precision, last sync and validation. Missing pairs
with specific reasons appear in `data/reports/lazyportfolio_returns_unresolved.csv`.
Statuses are `completed`, `pending`, `partial`, `failed`,
`not_available_in_backtester`, and `restricted`. A failed refresh can coexist
with a previously validated stored series; both states are reported separately.

The instrument CSV includes native-currency `returns_status`, `returns_downloaded`
and `return_observation_count`. Detailed additional currency coverage is in the
pair CSV. If another program locks the instrument CSV, an updated copy is saved as
`lazyportfolio_instruments.updated.csv`; the committed database import is preserved.

### Compact local storage

```text
data/
  qcross.db                   # authoritative instruments, series, imports and revisions
  exports/                    # catalog CSV/JSON and combined returns CSV/Parquet
    pairs/                    # explicitly requested individual pair exports
  reports/                    # coverage, pilot, catalog and latest validation reports
  logs/                       # returns.log / discovery.log, bounded rotation
  cache/lazyportfolio/        # backtester_pairs.json and individual_pages.json
  archive/validated_sources.zip # only sources referenced by stored return series
  snapshots/lazyportfolio/   # created only when --save-raw is requested
```

The combined returns files contain all stored ticker/currency pairs; a separate
file per pair is unnecessary. `exports/pairs/` is absent unless explicitly requested.
`returns export --symbol VTI` selects VTI's
native currency and writes to `exports/pairs/`; use `--currency` for another pair.
Completed ingestion retains its full validation in `return_series.metadata`,
without hundreds of redundant `returns_validation_*.json` files. Explicit
`validate` replaces `reports/latest_validation.json`. Automatic pilot checks
write one report; the explicit `backtester-pilot` command also writes candidate
exports under `reports/pilots/`, separate from database exports.

`CACHE_DIR`, `REPORT_DIR`, `LOG_DIR`, `ARCHIVE_DIR` and `RAW_SNAPSHOT_DIR` configure
these paths. Logs rotate at 5 MiB, keeping two backups. All runtime artifacts
remain ignored by Git. See [storage notes](docs/data-storage.md).

For an older installation, inspect `python -m scripts.compact_data`; use
`--apply` to archive loose sources and historical diagnostics and move known
outputs. Every source is checked against its archived SHA-256 before its loose
copy is removed; the SQLite database is not modified. The archive index supports
both the old relative and absolute snapshot paths, so offline source validation
continues to work without extracting thousands of files.

After that initial migration, `python -m scripts.compact_data --prune` previews
removal of obsolete investigation artifacts; add `--apply` to execute. It keeps
only database-referenced source pages in `archive/validated_sources.zip`, verifies
their SHA-256 before removing legacy archives, removes history logs, redundant
pair copies and one-off preservation audits, and reduces the known URL cache to
validated individual-page series. No new cleanup reports are written.

Long exports include date, ticker, currency, extraction_method, currency_kind, return,
instrument/series IDs, source,
quality and extraction time. CSV contains exact decimal text. Parquet has date32
and numeric float64 returns for direct Pandas/NumPy use; its schema metadata and
JSON sidecar explain source rounding and binary numeric conversion. Wide export
**requires** an explicit currency, includes only existing series in that currency,
and takes the union of their dates with null/blank missing cells. No FX conversion
and no zero-fill are performed. Other source/currency series remain separate.

```python
import pandas as pd

long_data = pd.read_parquet("data/exports/lazyportfolio_monthly_returns.parquet")
wide = pd.read_parquet("data/exports/lazyportfolio_monthly_returns_wide.parquet")
returns_matrix = wide.set_index("date").to_numpy(dtype=float)  # gaps are NaN
```

### Read-only returns API

- `GET /api/v1/return-series?instrument_id=1&currency=USD&source=lazyportfolioetf`
- `GET /api/v1/return-series/{id}`
- `GET /api/v1/return-series/{id}/observations?start_date=2000-01-01&end_date=2026-08-31`

Series and observations support bounded `limit`/`offset`; observations are ordered
chronologically. API return values are decimal **strings**, preserving precision.
No ingestion endpoint is exposed.

### Returns architecture and tests

```text
Public source HTML → shared HTTP client → LazyPortfolioETF JSON parser
                  → source validation/provenance → generic validated-series contract
                  → transactional repository/database → API or CSV/Parquet exports
```

All source assumptions stay in `app/ingestion/lazyportfolio/`, including the
dedicated `backtester.py`, `backtester_pilot.py` and `return_pairs.py` adapters.
`app/ingestion/returns.py` defines the provider-independent storage contract.
Future independently maintained market-data histories use another `source` and
remain separate from LazyPortfolioETF's reconstructions.

Regular `python -m pytest` excludes network tests and uses a sanitized fixture
with all 2,804 original VTI observations, 2,805 backtester VTI months, and 1,197
backtester VTV months. Opt-in live check:

```bash
python -m pytest -m network tests/integration
python scripts/sanitize_lazyportfolio_returns_fixture.py <raw-vti.html> \
  --source-url https://www.lazyportfolioetf.com/etf/vanguard-total-stock-market-vti/
```

The sanitizer removes tokens/nonces, tracking, daily/live values and unrelated data.
Review the replacement fixture and numerical differences before committing it.
Tests cover extraction/precision, date errors, cash-flow guards, full-path/statistical
validation, invalid values, revisions, transactional writes, incremental extension,
interruption/resume, exports, currency separation and API filters. No efficient
frontier, optimization, bootstrap, Monte Carlo or frontend work is included.
# Portfolio analytics (Task 3)

The backend now includes constrained efficient-frontier optimization, four covariance
estimators, classical K-fold CV with a weight ensemble, Stationary Bootstrap, weight
stability, resampled portfolios/frontiers, and an equal-weight reference.

- [Mathematical conventions and methodology](docs/analytics.md)
- [API, schemas, execution limits and project structure](docs/analytics-api.md)
- [Actual stored USD calculation, portfolios, stability and timings](docs/analytics-demo/report.md)
- [Staged verification and complete test results](docs/analytics-verification.md)

Install the numerical and test dependencies with the project's configured interpreter
using `-m pip install -e ".[dev]"`. Run the reproducible stored-history demonstration
with `-m scripts.analytics_demo`. Analytics reads existing validated return series;
historical data and ingestion mechanisms are unchanged.
