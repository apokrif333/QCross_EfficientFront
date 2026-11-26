# Portfolio simulation

Historical reporting defaults to target-weight rebalancing every 12 observations,
measured from the selected panel's first month. Holdings drift between events.
Month-end observations determine the signal; executed targets earn next month's
returns. The final observation skips the ordinary rebalance and settles any
remaining tax obligation.

`transaction_fee_rate` is a decimal fraction (default zero). Each actual net buy
and sell is charged on its notional. Initial purchases are charged too: invested
wealth is 1/(1+fee) from an initial dollar. The first net monthly return includes
this initial cost, and initial wealth remains 1 in CAGR and drawdown calculations.
Execution solves post-trade invested wealth plus commission equals pre-trade
wealth; no borrowing or external capital is introduced. Turnover is annualized
two-sided traded notional divided by pre-trade wealth, excluding initial entry.

Gross and net paths are simulated separately using the same signals and targets.
Net metrics use actual net monthly returns; gross/net differences include the
subsequent investment effect of fees and taxes. Tax funding by itself leaves
Gross positions alone. Portfolio historical volatility uses
sample standard deviation (ddof=1). Sharpe uses aligned monthly BIL differential
returns. Sortino uses sqrt(12)*mean(d)/sqrt(mean(min(d,0)^2)), with d the monthly
portfolio-minus-BIL return over all months. Zero denominators return null.

The optimizer still uses monthly arithmetic asset means and estimated asset or
differential covariances. Model `expected_return`, `volatility`, `sharpe_ratio`
retain their meanings. Historical fields now reflect the simulated net path;
`gross_historical_metrics` and `trading_costs` disclose the counterfactual gross
path and costs. Bootstrap paths retain the selected-history length and all
sampling/pairing indices. Historical Train and Validation simulations each
start with a new allocation and independent tax accounting. The combined
Bootstrap Validation trades continuously across Fold boundaries.

## Annual capital gains tax

Engine 4.3.0 uses `annual_net_realized_average_cost_v1`. Initial and later
buys add the purchase notional plus its fee to the position's total basis.
Sales allocate basis in proportion to the part sold and subtract selling fees
from proceeds. A full exit clears the basis. The simulator adds each signed
realized result to the current year's balance. Basis and that balance are
deflated to the current month in Real mode, which gives the same nominal tax
when translated back. The simulation treats the entire monthly total return
as a price movement of an accumulating fund.

Historical calendars close in December and on the final observation. Synthetic
calendars follow successive month positions in the source period before
resampling. Each synthetic Train anchors to its own source Train start, and
the concatenated Validation anchors to the first source month. Inflation
factors follow the sampled return rows. The tax calendar is shared by all
policies and lookbacks and independent of the sampled dates. Fold changes keep
holdings, basis, annual gain and signal state. A Fold change at December uses
one execution with the other trades and the tax settlement.

At each close, the engine solves for posttrade NAV with the fees and tax on
the actual net trades, including any sales needed to pay the tax. An ordinary
trade uses its strategy target. A tax-only event sells pro rata to preserve
the drifted allocation. Only positive annual realized gain is taxed and the
annual balance resets. If there is insufficient capital, the engine sells
the available positions, pays what it can and reports the unpaid balance and
insolvency. A position with zero market value produces no sale proceeds or
modeled tax write-off. The ending NAV is after settlement without a general
liquidation of unrealized holdings.

Aggregate costs sum the actual commissions and paid tax in the selected
return basis. Annualized costs add each expense divided by its pretrade NAV
and scale the sum by `12 / months`. Initial commissions use NAV of 1. A
combined trade uses one denominator for the two expenses. Tax-only sales add
fees, turnover and trade counts but do not add to the rebalance count. The
event log exposes the trigger, positions' basis, purchases, sales, pretrade
and posttrade NAV, realized results, close basis, paid tax and unpaid tax. In
Real mode, multiply an event amount by cumulative inflation through that month
to recover its nominal amount.

Older snapshots and bundled Demo calculations retain their original values.
The UI identifies the legacy tax methodology and leaves unavailable metrics
absent. Comparisons and cache reuse check the methodology before mixing
results. The model is a research convention, independent of any jurisdiction.

`python -m scripts.benchmark_annual_tax` measures the separate Bootstrap trend
follow-up on 608 months, 500 retained joined Validation draws, four folds,
12-month expected blocks, five assets including BIL, and 0.2% fees. It sweeps
lookbacks 1–36 for the portfolio and four managed assets and reports the median
of three runs. Both versions used the same sampled indices and one numerical
thread. The time excludes fitting the original Bootstrap portfolios.

| Tax rate | Before (`46f0c10`) | Annual model | Change in elapsed time |
| --- | ---: | ---: | ---: |
| 0% | 20.090 s | 20.109 s | +0.1% |
| 10% | 41.985 s | 25.751 s | −38.7% |

Requests may specify `rebalancing_interval_months` (default 12; null means hold).
Old saved UI calculations replay with interval 1 and fee zero when these fields
were absent. Legacy snapshots are not relabeled as annual simulations. New Live
requests use annual rebalancing. Engine 3.0.0 and its digest include the simulator.

Solver allocations can contain residual negative weights within the optimizer's
existing 1e-7 feasibility tolerance. Only reporting of such solver residuals may
clip and renormalize for nonnegative execution, with the maximum correction
reported as `execution_target_adjustment`. Explicit user weights remain validated
as supplied; materially invalid targets are rejected. Insolvency produces a
-100% monthly loss followed by zero returns and zero holdings, without leverage.

Policies consume a current-month state, with separate target-provider,
capital-destination and trade-executor interfaces. No SMA, Momentum, downside
volatility strategy or external hedge data was calculated by engine 3.0.
Engine 4.x adds [optional portfolio controls](portfolio-controls.md) through these
same interfaces, preserving legacy mode-none behavior.
