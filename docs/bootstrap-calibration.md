# Bootstrap Block Length Calibration

Calibration evaluates how well circular Stationary Bootstrap forecasts reproduce later market observations. It uses selected USD monthly total-return series, independent of portfolio weights, trading rules, covariance estimators, taxes, and the existing bidirectional Bootstrap.

## How to run

Open **Bootstrap Calibration**. Live uses the assets selected in the common asset picker. The tab has its own date limits and a Nominal/Real choice. Blank date limits use the longest available common history. Verify the chronological periods first, then run **Calibration**. Use **Final Holdout** to evaluate the fixed winner after Calibration. **Use Selected Block Length** copies the selected length into the main Bootstrap settings without submitting a portfolio job.

Defaults are 240 months of Train, 240 months of Test and a step equal to Test length, 1,000 paths for each integer L from 3 to 24, reference L=12, seed 42, and a practical 5% tolerance. All seven horizons are initially active (3, 6, 12, 24, 60, 120, and 240 months). Demo includes frozen nominal results for VTI, TLT, GLD, BIL, and BND, with one example Fold/horizon/asset in the detailed inspector for each phase. Live retains all available drill-downs while the relevant job is in memory.

The candidate grid supports block lengths from 1 to 24 months. With L=1, each month is sampled independently and all assets share the same historical month.

## Chronology and sources

- Only complete, fixed-length Train blocks are used. The first Train starts at the first eligible month. Each Test starts immediately after its Train. Step is at least Test length, so Tests do not overlap. An earlier Test can later be part of Train.
- The last available Test is reserved when Holdout is enabled. At least one full Calibration Test is required. A partial Test contributes only complete, non-overlapping intervals for a particular horizon. A Holdout shorter than every selected horizon is reported as unavailable.
- A forecast for a given Fold and horizon stays unconditional across its real intervals. It always uses the same Train. Earlier complete Calibration Folds are included even when they perform poorly. Short and long horizons can reuse months.
- Calibration loads the validated common history and blocks gaps. BIL is sampled like any other selected asset. Real monthly returns are computed from aligned returns and inflation as `(1+r)/(1+inflation)-1`. Indexing those real returns is mathematically equivalent to resampling their matching raw returns and inflation together. The module rejects gaps and the shared loader's unpublished zero placeholders. Select an earlier end date explicitly if needed.
- Source identifiers, versions, metadata, and reconstruction warnings accompany the result. A pre-inception reconstruction is an input series, with no implication that the corresponding ETF was then investable.

## Scenario generation and ranking

The batched generator starts at a uniformly drawn Train position. Every subsequent position restarts with probability 1/L and otherwise advances one row, wrapping at the end of Train. This is equivalent to geometrically distributed circular blocks. All assets use the same indices. For each Fold, candidates share start positions and restart uniforms from `SeedSequence([seed, fold, 0])`. A separate reference stream uses flag 1. Each horizon reuses a prefix of the same path. The main Bootstrap sampler and validation split keep their existing implementation.

The empirical CRPS is the mean distance to the observed value minus half of the mean distance between all ordered forecast pairs. Sorted simulations, cumulative sums and binary searches provide the same answer without an N × N matrix. Within each Fold, horizon, statistic and asset or pair, every L uses the same Train-only reference scale. Use the reference IQR when it reaches the predefined floor, otherwise its population standard deviation when that reaches the floor, otherwise the floor. The result reports the number of floor applications and the exact scale and method in drill-downs.

Every real interval contributes equally within a statistic and member. Next average over admissible assets or pairs, then equally over primary statistics within a group. Average applicable groups equally within each Fold and horizon. Average Folds equally for each horizon and then average the available horizons equally. Calibration chooses the minimum, with ties favoring the smaller L. The practical set contains every L at or below `minimum × (1 + tolerance)`, even if it is discontinuous. Relative differences are unavailable for a positive score when the minimum is zero. The sensitivity summaries remove one Fold or horizon at a time using the previous scores.

## Path characteristics

| Group | Primary characteristics | Applicability or scale |
| --- | --- | --- |
| Return Distribution | Compounded return | All selected horizons. Floor 0.01 in return units. CAGR and annualized arithmetic return are displayed for diagnosis. |
| Risk Distribution | Sample volatility, zero-target downside RMS, negative-month frequency, monthly P5, skewness, excess kurtosis | Volatilities annualize with √12. Monthly P5 and skewness require 12 months. Excess kurtosis requires 24. Skewness and excess kurtosis use population central moments. Floors are 0.01 for volatilities, 0.05 for frequency, 0.005 for monthly P5 and 0.1 for moments. |
| Temporal Dependence | Pearson ACF of returns, absolute returns, squared returns and signs, through lag 12 | At least eight paired observations at each lag, constant inputs undefined. Floor 0.05. |
| Market Cycles and Drawdowns | Maximum drawdown, longest drawdown, fraction of months underwater, longest positive and negative streaks, longest trailing three-month trend | NAV starts at 1, included in the initial peak. Longest ongoing drawdowns count through the interval end. Trends use the sign of trailing three-month compounded returns, require at least six months and count consecutive trailing signals. Recovery from the deepest trough is diagnostic, with terminal censoring recorded alongside elapsed recovery time. Floors are 0.01 for drawdown, 0.05 for fractions and one month for durations. |
| Cross-Asset Dependence | Pearson monthly, absolute and three-month return correlations, joint negative frequency, stressed joint negative frequency | Correlations require six paired observations and nonzero variation. The three-month returns use non-overlapping compounded blocks. Stress is a cross-asset mean of Train-standardized monthly returns below −1 and requires at least three stressed observations. Floors are 0.05. |

A feature with no finite reference scenario or no finite real observation contributes no CRPS for any L. Undefined real observations are displayed as missing. For an otherwise applicable feature, an undefined synthetic statistic is recorded and assigned the fixed neutral value zero. This convention keeps the ensemble size and the set of scored features constant across candidates. It can matter for short, nearly constant or rarely stressed series.

## Supplementary scores and figures

- **Energy Score** uses the exact empirical formula with Euclidean distance between vectors of compounded returns, scaled with the same reference as CRPS. The forecast-pair term is calculated in 128-row blocks.
- **Variogram Scores** use p=0.5. Forecast expectations use every scenario. Signed and absolute returns are centered and rescaled using the corresponding Train mean and population standard deviation with a one-basis-point floor. The temporal scores weight lags 1–12 equally when available, then all month positions and assets equally within a lag. The cross-asset Score weights unordered pairs and months equally. Each family's total pair weight is one. Energy and Variogram are displayed separately from the CRPS ranking.
- **PIT and coverage** use empirical midranks for ties. Coverage is inclusive of the actual estimated percentile boundaries. PIT, coverage and median absolute deviation from the 50th percentile describe the calibration of the main predicted features. Raw mean CRPS is shown at the individual statistic level because units differ across features.
- **Distribution inspector** shows Train and Test intervals, forecast quantiles, per-interval ranks, CRPS and normalized CRPS. It displays 32-bin forecast histograms and ACF profiles with P5–P95 bands. Forecast samples are accumulated in 128-path batches. The browser loads compact summaries only when needed. Switching charts does not regenerate scenarios.

## API, jobs and reproducibility

| Method and path under `/api/v1/analytics` | Result |
| --- | --- |
| POST `/bootstrap-calibration/preview` | Common period, sources, period counts, eligible folds and workload. |
| POST `/bootstrap-calibration` | Submit Calibration with the preview settings. |
| POST `/bootstrap-calibration-holdout` | Supply `calibration_job_id` for a completed Calibration. Only the winner in that job is used, regardless of what L is currently displayed. |
| GET `/jobs/{job_id}` or `/jobs/{job_id}/result` | Get progress or the aggregate result. |
| GET `/bootstrap-calibration/{job_id}/detail` | Pass `fold`, `horizon`, and repeated `metrics`, `member` and `lengths` query values. Use one member for an asset, two for a pair. |
| DELETE `/bootstrap-calibration/{job_id}` | Cancel a running Calibration or Holdout worker. |

Results use `chronological_bootstrap_block_length_calibration_v1`. The result JSON includes algorithms, scales policy, input roles, weights, source hashes, Fold bounds, seed, calculation identity and sample fingerprints. Sample fingerprints hash little-endian, row-major 32-bit Train positions. Recreating a path requires matching settings and source, regenerating the indices for that Fold and L and selecting its zero-based row. The calibration identity hashes the evaluated Train/Test windows and their dates, so changes only to future Holdout observations leave that identity and the ranking intact. The full loaded source still has its own provenance hashes.

Workers observe the existing bounded job registry and retention policy. Calibration gets at least 600 seconds per job. Preview rejects more than 1 billion simulated asset-months (including reference draws) or more than 3,000 score rows. Viewing details or launching Holdout requires the original retained result, so download aggregate results for durable storage. The API tracks recently evaluated overlapping Holdout periods for the same assets in the running server. The browser also tracks the last 100 views locally and marks reuse after calibration settings change. This history cannot establish whether a period was explored on another server or in another browser.

## Local benchmark

Run from the repository root with an existing validated QCross SQLite database.

```powershell
.venv/Scripts/python.exe -m scripts.benchmark_bootstrap_calibration
.venv/Scripts/python.exe -m scripts.benchmark_bootstrap_calibration --real --end-date 2025-09-30
.venv/Scripts/python.exe -m scripts.benchmark_bootstrap_calibration --demo docs/analytics-demo/bootstrap-calibration.json
```

Each run used four full Calibration Folds, the seven default horizons, 22 block lengths, 1,000 scenarios per L and Fold, all five demo assets, and one native numerical thread. Baseline and optimized nominal runs used the same input and seed. The optimization makes time contiguous for ACF reductions and preserves the formulas. Times are local single-run wall times for the engine, excluding database load, worker startup, API transfer and browser rendering.

| Run | Common history | Calibration | Holdout | Calibration winner | Min CRPS |
| --- | --- | ---: | ---: | ---: | ---: |
| Nominal before ACF optimization | 1919-01 to 2026-08 | 293.22 s | 1.22 s | 22 | 1.156233 |
| Nominal after ACF optimization | 1919-01 to 2026-08 | 94.04 s | 0.54 s | 22 | 1.156233 |
| Real after ACF optimization | 1919-01 to 2025-09 | 96.42 s | 0.54 s | 8 | 0.968972 |

Nominal Calibration elapsed time fell 67.9%. A final Demo regeneration alongside the test suite took 103.62 s for Calibration and 0.59 s for Holdout, with the same scores. In the nominal example all 22 L were within the 5% tolerance. The Holdout had 92 months and a CRPS of 0.635701, evaluated through the five-year horizon. The Real end date avoids a documented unpublished October 2025 inflation observation in the local source.

## Interpretation and references

Stationary Bootstrap assumes that historical samples contain relevant information about future distributions. Structural breaks, regime changes, errors in reconstructed history and circular transitions can limit forecasts. Different assets, groups and horizons can favor different L. Four full folds provide four non-overlapping twenty-year comparisons, which may still be statistically dependent. Thousands of synthetic draws do not expand this historical evidence. The aggregate depends on methodology choices. The practical score tolerance provides no statistical confidence interval. The selected L calibrates scenario generation without guaranteeing future investment results.

The sources below describe individual methods. The QCross aggregation, metric applicability and scoring weights are project choices. The optional Politis–White automatic estimator is deferred to keep the module free of an additional `arch` dependency.

- [Politis and Romano (1994), The Stationary Bootstrap](https://doi.org/10.1080/01621459.1994.10476870)
- [Politis and White (2004), Automatic Block-Length Selection](https://doi.org/10.1081/ETC-120028836) and [Patton, Politis and White (2009), correction](https://doi.org/10.1080/07474930802459016)
- [Gneiting and Raftery (2007), Strictly Proper Scoring Rules](https://doi.org/10.1198/016214506000001437)
- [Scheuerer and Hamill (2015), Variogram-Based Proper Scoring Rules](https://doi.org/10.1175/MWR-D-14-00269.1)
- [Tashman (2000), Out-of-Sample Tests of Forecasting Accuracy](https://doi.org/10.1016/S0169-2070(00)00065-0) and [Cerqueira, Torgo and Mozetič (2020), Time Series Forecasting Evaluation](https://doi.org/10.1007/s10994-020-05910-7)
