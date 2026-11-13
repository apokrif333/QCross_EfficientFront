# Portfolio simulation

Historical reporting defaults to target-weight rebalancing every 12 observations,
measured from the selected panel's first month. Holdings drift between events.
Month-end observations determine the signal; executed targets earn next month's
returns. There is no terminal trade without a following observation.

`transaction_fee_rate` is a decimal fraction (default zero). Each actual net buy
and sell is charged on its notional. Initial purchases are charged too: invested
wealth is 1/(1+fee) from an initial dollar. The first net monthly return includes
this initial cost, and initial wealth remains 1 in CAGR and drawdown calculations.
Execution solves post-trade invested wealth plus commission equals pre-trade
wealth; no borrowing or external capital is introduced. Turnover is annualized
two-sided traded notional divided by pre-trade wealth, excluding initial entry.

Gross and net paths are simulated separately using the same signals and targets.
Net metrics use actual net monthly returns; gross/net differences include the
subsequent investment effect of fees. Portfolio historical volatility uses
sample standard deviation (ddof=1). Sharpe uses aligned monthly BIL differential
returns. Sortino uses sqrt(12)*mean(d)/sqrt(mean(min(d,0)^2)), with d the monthly
portfolio-minus-BIL return over all months. Zero denominators return null.

The optimizer still uses monthly arithmetic asset means and estimated asset or
differential covariances. Model `expected_return`, `volatility`, `sharpe_ratio`
retain their meanings. Historical fields now reflect the simulated net path;
`gross_historical_metrics` and `trading_costs` disclose the counterfactual gross
path and costs. Bootstrap paths retain the selected-history length and all
sampling/pairing indices. Each CV evaluation starts with a new allocation and
its own initial purchase cost; it is not stitched into a continuous trading path.

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
