import type { ApiRequest, BootstrapResult, Calculation } from "../types";
import { ANNUAL_TAX_METHODOLOGY, effectiveRequest, stable } from "./snapshot";

export function reusableControlCache(
  body: ApiRequest,
  calculations: Calculation[],
) {
  const fields = [
    "instrument_ids",
    "start_date",
    "end_date",
    "return_basis",
    "risk_free_source",
    "risk_free_rate",
    "portfolio_controls",
    "transaction_fee_rate",
    "capital_gains_tax_rate",
    "rebalancing_interval_months",
    "user_weights",
    "expected_block_length",
    "random_seed",
  ] as const;
  const target = effectiveRequest(body);
  for (const c of calculations) {
    if (c.kind !== "bootstrap") continue;
    const source = effectiveRequest(c.request);
    const result = c.result as BootstrapResult;
    if (result.methodology === "bidirectional_block_cv_stationary_bootstrap_v1")
      continue;
    const cache = result.portfolio_control_cache;
    if (
      cache?.tax_methodology === ANNUAL_TAX_METHODOLOGY &&
      fields.every((k) => stable(source[k]) === stable(target[k]))
    )
      return cache;
  }
  return undefined;
}
