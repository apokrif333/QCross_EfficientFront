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
