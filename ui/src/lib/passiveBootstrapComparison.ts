import type {
  BootstrapControlSample,
  BootstrapPassiveComparison,
  RiskReturn,
  SimulationStats,
} from "../types";
import { linearQuantile } from "./statistics";

const METRICS = [
  ["delta_arithmetic_return", "annualized_arithmetic_return"],
  ["delta_cagr", "annualized_cagr"],
  ["delta_volatility", "annualized_volatility"],
  ["delta_sharpe", "historical_sharpe_ratio"],
  ["delta_max_drawdown", "maximum_drawdown"],
] as const;

function finite(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function riskReturn(stats: Partial<SimulationStats>): RiskReturn | null {
  const { annualized_arithmetic_return, annualized_volatility } = stats;
  return finite(annualized_arithmetic_return) && finite(annualized_volatility)
    ? {
        expected_return: annualized_arithmetic_return,
        volatility: annualized_volatility,
      }
    : null;
}

export function comparePassiveBootstrap(
  samples: BootstrapControlSample[],
  horizon: number,
  weights?: Record<string, number> | null,
): BootstrapPassiveComparison {
  const clouds: BootstrapPassiveComparison["clouds"] = [];
  let valid = 0;
  const results: BootstrapPassiveComparison["results"] = samples.map(
    (sample) => {
      const active = sample.controlled?.net,
        passive = sample.baseline?.net;
      if (sample.status === "failed" || !active || !passive) return {};
      valid++;
      const activePoint = riskReturn(active),
        passivePoint = riskReturn(passive);
      if (activePoint && passivePoint)
        clouds.push({ active: activePoint, passive: passivePoint });
      const differences: Record<string, number> = {};
      for (const [delta, metric] of METRICS) {
        const a = active[metric],
          p = passive[metric];
        if (finite(a) && finite(p) && finite(p - a)) differences[delta] = p - a;
      }
      return { differences };
    },
  );
  const differences = Object.fromEntries(
    METRICS.map(([key]) => {
      const values = results
        .map((r) => r.differences?.[key])
        .filter(finite)
        .sort((a, b) => a - b);
      if (!values.length) return [key, null];
      return [
        key,
        {
          mean: values.reduce((sum, v) => sum + v / values.length, 0),
          median: linearQuantile(values, 0.5)!,
          p5: linearQuantile(values, 0.05)!,
          p95: linearQuantile(values, 0.95)!,
          minimum: values[0],
          maximum: values[values.length - 1],
          valid_count: values.length,
        },
      ];
    }),
  );

  function frequency(keys: string[], better: (...deltas: number[]) => boolean) {
    let wins = 0,
      denominator = 0;
    for (const row of results) {
      const deltas = keys.map((key) => row.differences?.[key]);
      if (!deltas.every(finite)) continue;
      denominator++;
      if (better(...deltas)) wins++;
    }
    return { fraction: denominator ? wins / denominator : null, denominator };
  }

  return {
    comparison_mode: "active_vs_passive",
    generated_datasets: samples.length,
    horizon_months: horizon,
    user_weights: weights ?? undefined,
    requested_comparisons: samples.length,
    valid_count: valid,
    failed_count: samples.length - valid,
    differences,
    win_frequencies: {
      higher_cagr: frequency(["delta_cagr"], (cagr) => cagr > 0),
      higher_sharpe: frequency(["delta_sharpe"], (sharpe) => sharpe > 0),
      lower_volatility: frequency(
        ["delta_volatility"],
        (volatility) => volatility < 0,
      ),
      lower_drawdown: frequency(
        ["delta_max_drawdown"],
        (drawdown) => drawdown < 0,
      ),
      higher_cagr_no_higher_volatility: frequency(
        ["delta_cagr", "delta_volatility"],
        (cagr, volatility) => cagr > 0 && volatility <= 0,
      ),
    },
    results,
    clouds,
  };
}
