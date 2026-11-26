# Bidirectional Block Cross-Validated Stationary Bootstrap

`POST /api/v1/analytics/bootstrap` uses `bidirectional_block_cv_stationary_bootstrap_v1`. The defaults are 500 generations, 4 folds, an expected block length of 12 months, and seed 42. Odd generation counts are valid.

The engine applies `one_sided_folds()` to the aligned historical panel first. Each fold draws a synthetic Train and Validation independently, with geometric blocks and circular wrapping within its own source interval. Joint row indexing preserves asset, hedge and BIL alignment in the selected Nominal/Real basis. Source indices are disjoint between Train and Validation in the same fold. The original Validation blocks cover the full panel in chronological block order. A two-fold configuration therefore needs at least 240 months. Each fold requires 120 Train and 60 Validation months. CV Linear also checks the eligibility of each inner split before the job starts.

At 608 months, 4 folds and 500 generations, the result uses 2,000 synthetic Train sets and 2,000 synthetic Validation blocks. The Train lengths are 456, 304, 304 and 456 months and each Validation block is 152 months. Each selected objective is fitted 2,000 times. Each generation's Validation blocks are concatenated in source block order, yielding 500 paths of 608 months each.

Each fit estimates moments from its synthetic Train. CV Linear jointly selects the shrinkage target (identity or diagonal) and intensity using Gaussian likelihood on inner validation splits entirely within that Train. The asset and BIL differential models each use their own inner search when BIL varies. A constant risk-free series shifts the mean and permits exact reuse of the same Train covariance. My Portfolio uses the managed portfolio's realized net Train volatility as the risk budget when controls are enabled. The ex-post Validation frontier is a descriptive reference.

The simulator starts once per path. At a change of optimized fold weights it trades from the current positions before the next month's return. The cost enters the return for the month ending at that boundary. A coincident calendar rebalance uses the same execution. Managed and passive portfolios keep their existing policies throughout and have no fold boundary trades triggered by the split. Wealth, peaks, signal history, rebalance calendar and cost accounting therefore span the full Validation. Annualized commissions sum costs relative to NAV immediately before each trade, including entry, then multiply by 12 divided by path length.

Each distribution has one observation per full Validation and exposes availability by metric. A failed fit prevents an optimized path for that generation. Valid user paths and passive comparisons remain available. Weight summaries pool fold weights from successful generations. These weights are diagnostics for separate fits and are never treated as one investable allocation. Trend sensitivity runs as an optional follow-up on the retained concatenated indices. It keeps the same draw for every lookback and reports a matching Validation fingerprint. Selecting a lookback based on these results is retrospective sensitivity analysis.

The response includes source fold bounds, per-generation fold lengths, unique counts, overlap, hashes, estimator parameters and optimization statuses. The compact lossless index representation is the versioned replay recipe and seed tuple `[seed, generation, fold, side]`, using PCG64, zero-based generation/fold IDs and side 0 for Train or 1 for Validation. `bootstrap_generation()` reconstructs the exact arrays and the stored fingerprints verify them. The trend job additionally retains the actual concatenated Validation arrays as uint16. Full index lists stay out of the browser payload.

Old snapshots keep their original values and carry a legacy-methodology notice. Snapshot comparisons distinguish the two methodologies. Resampled Frontier keeps its existing shared risk-aversion weight averaging on full-panel resamples. Its scenario cache belongs to that methodology and cannot consume the new fold Validation paths.

Bidirectional CV sometimes trains on periods later than the holdout and can reuse one fold's holdout period in another fold's Train. Resampling stays within the corresponding fold interval, but the stitched result is a synthetic scenario rather than a chronological walk-forward out-of-sample history. Five hundred generations represent five hundred synthetic scenarios, not independent historical experiments.

## Benchmark

Local comparison against the previous implementation at `250dd11` on a synthetic 608-month, three-asset panel (seed 2026, means 0.008/0.004/0.006, standard deviations 0.04/0.02/0.03). Each run used seed 42, 12-month blocks, GMV and Max Sharpe, one numerical thread, and no user portfolio.

| Estimator | Generations | Previous | Bidirectional K=4 | Complete generations per objective |
| --- | ---: | ---: | ---: | ---: |
| Sample | 500 | 10.02 s | 20.22 s | 500 / 500 |
| CV Linear | 25 | 0.56 s | 1.18 s | 25 / 25 |

The timings describe these inputs. Active controls, variable BIL and additional assets or folds add work. CV Linear evaluates the same candidate likelihoods across the intensity grid with batched linear algebra. Estimates are never shared across synthetic Train sets.
