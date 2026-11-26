import { expect, it } from "vitest";
import { comparePassiveBootstrap } from "../lib/passiveBootstrapComparison";
import type { BootstrapControlSample } from "../types";

it("subtracts active metrics from passive metrics within each draw and excludes missing pairs", () => {
  const samples: BootstrapControlSample[] = [
    {
      controlled: {
        annualized_capital_gains_tax: 0.004,
        annualized_total_costs: 0.006,
        net: {
          annualized_cagr: 0.05,
          annualized_volatility: 0.2,
          annualized_arithmetic_return: 0.08,
          maximum_drawdown: 0.3,
        },
      },
      baseline: {
        annualized_capital_gains_tax: 0.001,
        annualized_total_costs: 0.002,
        net: {
          annualized_cagr: 0.1,
          annualized_volatility: 0.15,
          annualized_arithmetic_return: 0.12,
          maximum_drawdown: 0.2,
        },
      },
    },
    {
      controlled: {
        net: {
          annualized_cagr: 0.2,
          annualized_volatility: 0.25,
          maximum_drawdown: 0.1,
        },
      },
      baseline: {
        net: {
          annualized_cagr: 0.1,
          annualized_volatility: 0.35,
          maximum_drawdown: 0.5,
        },
      },
    },
    {
      controlled: { net: { annualized_cagr: null, maximum_drawdown: 0.2 } },
      baseline: { net: { annualized_cagr: 0.4, maximum_drawdown: 0.2 } },
    },
    { status: "failed" },
    { baseline: { net: { annualized_cagr: 0.9 } } },
  ];
  const comparison = comparePassiveBootstrap(samples, 240);
  expect(comparison.valid_count).toBe(3);
  expect(comparison.failed_count).toBe(2);
  expect(comparison.requested_comparisons).toBe(5);
  expect(comparison.results[0].differences?.delta_cagr).toBeCloseTo(0.05);
  expect(comparison.results[1].differences?.delta_cagr).toBeCloseTo(-0.1);
  expect(comparison.results[2].differences?.delta_cagr).toBeUndefined();
  expect(comparison.differences.delta_cagr?.valid_count).toBe(2);
  expect(comparison.differences.delta_cagr?.median).toBeCloseTo(-0.025);
  expect(comparison.differences.delta_cagr?.p5).toBeCloseTo(-0.0925);
  expect(
    comparison.differences.delta_annualized_capital_gains_tax?.mean,
  ).toBeCloseTo(-0.003);
  expect(comparison.differences.delta_annualized_total_costs?.valid_count).toBe(
    1,
  );
  expect(comparison.win_frequencies.lower_annualized_total_costs).toEqual({
    fraction: 1,
    denominator: 1,
  });
  expect(comparison.win_frequencies.higher_cagr).toEqual({
    fraction: 0.5,
    denominator: 2,
  });
  expect(comparison.win_frequencies.lower_drawdown).toEqual({
    fraction: 1 / 3,
    denominator: 3,
  });
  expect(comparison.win_frequencies.higher_cagr_no_higher_volatility).toEqual({
    fraction: 0.5,
    denominator: 2,
  });
  expect(comparison.clouds).toEqual([
    {
      active: { expected_return: 0.08, volatility: 0.2 },
      passive: { expected_return: 0.12, volatility: 0.15 },
    },
  ]);
});
