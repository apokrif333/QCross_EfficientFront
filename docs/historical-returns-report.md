# Historical monthly returns: delivery report

Archived milestone 2 report. Its bulk counts predate Task 2.1 and are not current.
See [the backtester report](backtester-returns-report.md) for current pair coverage.

Verified 2026-10-01.

## Source and VTI pilot

Public URL: https://www.lazyportfolioetf.com/etf/vanguard-total-stock-market-vti/.
Original monthly percentages are embedded in `const _d`, at
`portfolios.VTI_1.rend.sliceArray.MAX.rendList`. The page is fetched once using HTTP;
no monthly-return API or browser automation is needed. ALL DATA selects the existing MAX slice.
`capitalBase` supplies nominal capital growth; `capitalNoInfl` is excluded.
The displayed table rounds to two percentage decimals; the original array supplies up to four.
The JavaScript accessor is `financials_dpGetMonthlyChart()` in `myLayout.js`.

**VTI USD: 2,804 month-end observations, 1793-01-31 through 2026-08-31. Validation PASS.**
Dividends are reinvested without dividend taxation, as stated on the page.
No incomplete/live observations, FX conversions, inflation adjustments or cash flows were imported.

| Metric | Calculated | Published | Result |
|---|---:|---:|---|
| final capital | 170710344.55736718 | 170708802.41860000 | PASS |
| annualized return | 8.45030654 | 8.45030000 | PASS |
| annualized volatility | 14.78254836 | 14.78250000 | PASS |
| maximum drawdown | -84.60240698 | -84.60240000 | PASS |

Final capital is the growth of an initial 1 USD; other metrics above are percentages.
Final growth relative error: 0.000009033739006599997411423.
Every one of the 2,804 capital points and 234 calendar-year windows passed precision-aware checks.
The final relative error is about **0.000903374%**, consistent with source rounding.
Population monthly volatility is annualized with sqrt(12). This is internal consistency checking,
not verification against an independent provider. Detailed annual returns and latest 12 months
are in `data/exports/VTI_USD_validation.json`.

The page labels returns through December 2001 as derived from equivalent ETFs/assets.
Those months are marked historical proxies. Later months are reported ETF-era observations
according to that broad statement; exact underlying-source transitions remain unknown.

## Bulk coverage

**115 / 263 instruments successful; 148 unsupported; 0 partial; 0 failed.**
**111,607 observations** were checked against the preserved raw responses and stored values.
Series retain source percentages rounded to at most four decimals. Latest published month:
2026-08-31: 66 series, 2026-09-30: 49 series.
The wide matrix keeps these unequal publication endpoints as null gaps.
Native currencies: CAD: 25, EUR: 32, GBP: 16, USD: 42.

Unsupported means the current public-link resolver did not identify a matching return detail page.
It does not mean these instruments have no returns in the simulator. No observations were invented
for them. Exact identifiers, including synthetic Bitcoin symbols, remain unchanged in the catalogue.
Source URLs came from published ranking tables and correlation tables on validated detail pages.
Dividend-only pages were followed via their explicit Historical Returns link.
The discovered `load-site-portfolios` API returned 403 and was not used or bypassed.

Published access conditions and source precision are documented in README.md.
Robots has no disallow rule; the privacy/terms page provides no explicit redistribution licence.

## Files and migrations

- `0003_monthly_returns`: series, observations, revision audit and persistent import checkpoints.
- `data/exports/VTI_USD_monthly_returns.csv` and `.parquet`: complete VTI series.
- `data/exports/lazyportfolio_monthly_returns.csv` and `.parquet`: all saved native series.
- `data/exports/lazyportfolio_monthly_returns_wide.parquet`: USD-only matrix, null gaps.
- `data/exports/lazyportfolio_returns_coverage.json`: all 263 instruments and status details.
- `data/exports/lazyportfolio_returns_audit.json`: raw/database comparison result.
- `data/exports/VTI_USD_validation.json`: full validation report.

CSV stores decimal text; Parquet uses numeric float64 for Pandas/NumPy, with precision metadata.
Wide export uses a union of dates and leaves missing values null, never zero.
Repeated VTI live update inserted 0, updated 0, found 0 revisions and preserved all 2,804 rows.

## Verification

- Deterministic pytest: **99 passed**, 1 network test deselected.
- Opt-in live integration: **1 passed**.
- Ruff check and formatting: passed.
- Alembic upgrade applied; schema check: no pending operations; upgrade/downgrade tested on SQLite.
- CSV/Parquet VTI round trip: all 2,804 dates and numeric observations match.
- One external Starlette/httpx TestClient deprecation warning; no parser validation errors.
- PostgreSQL schema is supported but no live PostgreSQL instance was used for these tests.

## Updated project tree

```text
app/
  __init__.py
  api/__init__.py
  api/dependencies.py
  api/router.py
  api/routes/__init__.py
  api/routes/health.py
  api/routes/instruments.py
  api/routes/returns.py
  cli/__init__.py
  cli/lazyportfolio.py
  cli/main.py
  cli/returns.py
  config.py
  db/__init__.py
  db/base.py
  db/models/__init__.py
  db/models/ingestion_state.py
  db/models/instrument.py
  db/models/returns.py
  db/session.py
  exports/__init__.py
  exports/returns.py
  ingestion/__init__.py
  ingestion/base.py
  ingestion/lazyportfolio/__init__.py
  ingestion/lazyportfolio/client.py
  ingestion/lazyportfolio/models.py
  ingestion/lazyportfolio/parser.py
  ingestion/lazyportfolio/returns_models.py
  ingestion/lazyportfolio/returns_parser.py
  ingestion/lazyportfolio/returns_service.py
  ingestion/lazyportfolio/returns_validation.py
  ingestion/lazyportfolio/service.py
  ingestion/lazyportfolio/validation.py
  ingestion/returns.py
  main.py
  repositories/__init__.py
  repositories/instruments.py
  repositories/returns.py
  schemas/__init__.py
  schemas/instrument.py
  schemas/returns.py
alembic/
  env.py
  script.py.mako
  versions/0001_instruments.py
  versions/0002_currency_availability.py
  versions/0003_monthly_returns.py
scripts/
  sanitize_lazyportfolio_fixture.py
  sanitize_lazyportfolio_returns_fixture.py
tests/
  __init__.py
  conftest.py
  fixtures/lazyportfolio_asset_universe.html
  fixtures/lazyportfolio_asset_universe.metadata.json
  fixtures/lazyportfolio_vti_returns.html
  fixtures/lazyportfolio_vti_returns.metadata.json
  ingestion/__init__.py
  ingestion/lazyportfolio/__init__.py
  ingestion/lazyportfolio/helpers.py
  ingestion/lazyportfolio/test_client.py
  ingestion/lazyportfolio/test_parser.py
  ingestion/lazyportfolio/test_returns.py
  ingestion/lazyportfolio/test_service.py
  ingestion/lazyportfolio/test_sync.py
  ingestion/lazyportfolio/test_validation.py
  integration/test_lazyportfolio_returns_live.py
  test_api.py
  test_cli.py
  test_fixture_sanitizer.py
  test_migrations.py
data/
  raw/lazyportfolio/returns/ (ignored source snapshots and URL index)
  exports/ (ignored generated CSV, Parquet, validation and coverage reports)
docs/
  historical-returns-report.md
pyproject.toml
alembic.ini
.env.example
README.md
```

## Successful instrument coverage

| Symbol | Currency | First month | Last month | Observations | Percentage decimals |
|---|---|---|---|---:|---:|
| VTI | USD | 1793-01-31 | 2026-08-31 | 2804 | 4 |
| VUN.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| SPY | USD | 1793-01-31 | 2026-08-31 | 2804 | 4 |
| VFV.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| SXR8.DE | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| CSP1.L | GBP | 1793-01-31 | 2026-09-30 | 2805 | 4 |
| SSO | USD | 1885-04-30 | 2026-08-31 | 1697 | 4 |
| IJH | USD | 1927-01-31 | 2026-09-30 | 1197 | 4 |
| XMC.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| ZPRV.DE | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| USSC.L | GBP | 1927-01-31 | 2026-08-31 | 1196 | 4 |
| IJR | USD | 1927-01-31 | 2026-09-30 | 1197 | 4 |
| XSMC.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| ZPRR.DE | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| R2SC.L | GBP | 1927-01-31 | 2026-08-31 | 1196 | 4 |
| XVLU.TO | CAD | 1975-01-31 | 2026-08-31 | 620 | 4 |
| QDVI.DE | EUR | 1975-01-31 | 2026-08-31 | 620 | 4 |
| IUVF.L | GBP | 1975-01-31 | 2026-09-30 | 621 | 4 |
| SXRT.DE | EUR | 1972-02-29 | 2026-08-31 | 655 | 4 |
| XIC.TO | CAD | 1985-01-31 | 2026-08-31 | 500 | 4 |
| VT | USD | 1970-01-31 | 2026-08-31 | 680 | 4 |
| IUSQ.DE | EUR | 1970-01-31 | 2026-09-30 | 681 | 4 |
| EUNL.DE | EUR | 1972-01-31 | 2026-09-30 | 657 | 4 |
| EFA | USD | 1970-01-31 | 2026-09-30 | 681 | 4 |
| XEF.TO | CAD | 1970-01-31 | 2026-08-31 | 680 | 4 |
| EFV | USD | 1975-01-31 | 2026-09-30 | 621 | 4 |
| IWFV.L | GBP | 1972-01-31 | 2026-08-31 | 656 | 4 |
| IS3S.DE | EUR | 1972-01-31 | 2026-09-30 | 657 | 4 |
| IMTM | USD | 2009-08-31 | 2026-09-30 | 206 | 4 |
| FCIM.NE | CAD | 2009-08-31 | 2026-09-30 | 206 | 4 |
| EEM | USD | 1976-01-31 | 2026-09-30 | 609 | 4 |
| XEC.TO | CAD | 1976-01-31 | 2026-08-31 | 608 | 4 |
| IS3N.DE | EUR | 1976-01-31 | 2026-09-30 | 609 | 4 |
| EMIM.L | GBP | 1976-01-31 | 2026-09-30 | 609 | 4 |
| VPL | USD | 1970-01-31 | 2026-08-31 | 680 | 4 |
| VA.TO | CAD | 1970-01-31 | 2026-08-31 | 680 | 4 |
| AAXJ | USD | 1985-01-31 | 2026-09-30 | 501 | 4 |
| HPRO.L | GBP | 1986-01-31 | 2026-09-30 | 489 | 4 |
| DWX | USD | 1970-01-31 | 2026-09-30 | 681 | 4 |
| DLS | USD | 1975-01-31 | 2026-09-30 | 621 | 4 |
| PFF | USD | 1992-01-31 | 2026-08-31 | 416 | 4 |
| ZUP.TO | CAD | 1992-01-31 | 2026-08-31 | 416 | 4 |
| PRFD | EUR | 1992-01-31 | 2026-08-31 | 416 | 4 |
| QQQ | USD | 1939-01-31 | 2026-08-31 | 1052 | 4 |
| QQC.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| SXRV.DE | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| VNQ | USD | 1928-01-31 | 2026-08-31 | 1184 | 4 |
| IQQ7.DE | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| XLE | USD | 1985-01-31 | 2026-08-31 | 500 | 4 |
| QDVF.DE | EUR | 1985-01-31 | 2026-08-31 | 500 | 4 |
| IESU.L | GBP | 1985-01-31 | 2026-09-30 | 501 | 4 |
| QDVG.DE | EUR | 1985-01-31 | 2026-08-31 | 500 | 4 |
| XLK | USD | 1999-01-31 | 2026-08-31 | 332 | 4 |
| QDVE.DE | EUR | 1999-01-31 | 2026-08-31 | 332 | 4 |
| IITU.L | GBP | 1999-01-31 | 2026-09-30 | 333 | 4 |
| QDVA.DE | EUR | 1982-01-31 | 2026-08-31 | 536 | 4 |
| BND | USD | 1793-01-31 | 2026-09-30 | 2805 | 4 |
| ZUAG.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| EUNX.DE | EUR | 1953-08-31 | 2026-09-30 | 878 | 4 |
| BSV | USD | 1871-01-31 | 2026-09-30 | 1869 | 4 |
| VGTY.DE | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| BIL | USD | 1793-01-31 | 2026-09-30 | 2805 | 4 |
| ZUCM.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| SHY | USD | 1793-01-31 | 2026-08-31 | 2804 | 4 |
| IUSU.DE | EUR | 1953-08-31 | 2026-09-30 | 878 | 4 |
| IEF | USD | 1793-01-31 | 2026-09-30 | 2805 | 4 |
| IUSM.DE | EUR | 1953-08-31 | 2026-09-30 | 878 | 4 |
| TLT | USD | 1793-01-31 | 2026-08-31 | 2804 | 4 |
| XTLT.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| IS04.DE | EUR | 1953-08-31 | 2026-09-30 | 878 | 4 |
| TIP | USD | 1985-01-31 | 2026-08-31 | 500 | 4 |
| XSTP.TO | CAD | 1985-01-31 | 2026-08-31 | 500 | 4 |
| CWB | USD | 1957-01-31 | 2026-09-30 | 837 | 4 |
| NUBD | USD | 1993-05-31 | 2026-08-31 | 400 | 4 |
| UC98.L | GBP | 1993-05-31 | 2026-08-31 | 400 | 4 |
| LQD | USD | 1793-01-31 | 2026-09-30 | 2805 | 4 |
| XCBU.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| UC84.L | GBP | 1793-01-31 | 2026-08-31 | 2804 | 4 |
| HYG | USD | 1919-02-28 | 2026-09-30 | 1292 | 4 |
| ZJK.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| IS0R.DE | EUR | 1953-08-31 | 2026-09-30 | 878 | 4 |
| SPLB | USD | 1919-02-28 | 2026-08-31 | 1291 | 4 |
| MBB | USD | 1985-01-31 | 2026-09-30 | 501 | 4 |
| SYBA.DE | EUR | 1987-07-31 | 2026-08-31 | 470 | 4 |
| EUN6.DE | EUR | 1980-07-31 | 2026-09-30 | 555 | 4 |
| IBCL.DE | EUR | 1983-02-28 | 2026-09-30 | 524 | 4 |
| IBCI.DE | EUR | 1989-10-31 | 2026-09-30 | 444 | 4 |
| XHYG.DE | EUR | 1989-07-31 | 2026-08-31 | 446 | 4 |
| ZAG.TO | CAD | 1988-01-31 | 2026-08-31 | 464 | 4 |
| ZFL.TO | CAD | 1994-05-31 | 2026-08-31 | 388 | 4 |
| ZRR.TO | CAD | 2010-07-31 | 2026-08-31 | 194 | 4 |
| CVD.TO | CAD | 2011-08-31 | 2026-09-30 | 182 | 4 |
| XCB.TO | CAD | 2007-01-31 | 2026-08-31 | 236 | 4 |
| UKCO.L | GBP | 1854-02-28 | 2026-08-31 | 2071 | 4 |
| BNDX | USD | 1985-01-31 | 2026-09-30 | 501 | 4 |
| EUNU.DE | EUR | 1972-08-31 | 2026-09-30 | 650 | 4 |
| EMB | USD | 1985-01-31 | 2026-09-30 | 501 | 4 |
| IUS7.DE | EUR | 1985-01-31 | 2026-09-30 | 501 | 4 |
| WIP | USD | 1985-01-31 | 2026-08-31 | 500 | 4 |
| GLD | USD | 1793-01-31 | 2026-09-30 | 2805 | 4 |
| ZGLD.TO | CAD | 1953-08-31 | 2026-08-31 | 877 | 4 |
| PHAU | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| SGLN.L | GBP | 1793-01-31 | 2026-08-31 | 2804 | 4 |
| UGL | USD | 1968-04-30 | 2026-08-31 | 701 | 4 |
| SLV | USD | 1915-01-31 | 2026-08-31 | 1340 | 4 |
| PHAG | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| SSLN.L | GBP | 1915-01-31 | 2026-08-31 | 1340 | 4 |
| DBC | USD | 1871-01-31 | 2026-09-30 | 1869 | 4 |
| CCOM.TO | CAD | 1953-08-31 | 2026-09-30 | 878 | 4 |
| UIQK.DE | EUR | 1953-08-31 | 2026-08-31 | 877 | 4 |
| CMOP.L | GBP | 1871-01-31 | 2026-09-30 | 1869 | 4 |
| GSG | USD | 1871-01-31 | 2026-09-30 | 1869 | 4 |
| GLTR | USD | 1970-01-31 | 2026-09-30 | 681 | 4 |
| DBMF | USD | 1988-01-31 | 2026-09-30 | 465 | 4 |
| DBMG.L | GBP | 1988-01-31 | 2026-09-30 | 465 | 4 |

## Unsupported instruments

XD9U.DE, XDUS.L, VTV, VUG, VOE, SPY4.DE, SPX4.L, IJK, IJS, IJT, IUSV, IUSG, VUKG.L, CS51.L, SSAC.L, XAW.TO, URTH, SWDA.L, MWEP.L, MWEQ.DE, WEXE.DE, XMWX.L, SCZ, IUSN.DE, WLDS.L, IWFM.L, IS3R.DE, IS3Q.DE, IWFQ.L, VGK, VE.TO, CPJ1.L, SXR1.DE, REET, CGR.TO, SPY2.DE, VHYG.L, VGWE.DE, PRAC.L, ESGV, XUSR.TO, XZMU.DE, XESU.L, CNX1.L, IUSP.L, XLC, IU5C.DE, GXLC.L, XLY, QDVK.DE, ICDU.L, XLP, 2B7D.DE, ICSU.L, XLF, XUSF.TO, QDVH.DE, UIFS.L, XLV, FHH.TO, IHCU.L, XLI, FHG.TO, 2B7C.DE, IISU.L, XLB, 2B7B.DE, IMSU.L, XLU, 2B7A.DE, IUSU.L, MTUM, XMTM.TO, IUMF.L, RSP, EQL.TO, XDEW.DE, XDWE.L, USMV, XMU.TO, IBCK.DE, MVUS.L, QUAL, XQLT.TO, QDVB.DE, IUQF.L, VYM, XHU.TO, USDV.DE, USDV.L, DES, OEF, SNP1.DE, SUAG.L, GOVT, VUTA.L, XFFE.DE, BBM3.L, ZTS.NE, CU31.L, IEI, ZTM.NE, SXRL.DE, CU71.L, HTB.TO, IBTM.L, UST, IBTL.L, IUST.DE, ITPS.L, SPPU.DE, VUCE.DE, SHYU.L, SMBS.L, XEON.DE, XGLE.DE, LYQ2.DE, SXRP.DE, LYXD.DE, EUN4.DE, EUN5.DE, XGB.TO, CBIL.TO, ZFS.TO, CLG.TO, ZFM.TO, XSAB.TO, IGLT.L, XSTR.L, IGL5.L, GLTL.L, INXG.L, IS15.L, BNDX--CAD, SAGG.L, XG7S.DE, XG7S.L, EMB--CAD, VEMA.L, SGIL.L, IUS5.DE, GLCB.L, ZPRC.DE, ^BTC, ^BTC-CAD, ^BTC-EUR, ^BTC-GBP, DBMF.DE

No efficient-frontier, optimization, bootstrap, Monte Carlo, frontend or scheduled jobs were added.
