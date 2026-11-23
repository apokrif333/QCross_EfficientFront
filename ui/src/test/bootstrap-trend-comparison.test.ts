import { expect, it } from "vitest";
import { summarizeBootstrapTrend } from "../components/BootstrapTrendComparison";
import type { BootstrapTrendSeries } from "../types";

it("uses matched draws, per-draw Calmar and metric-specific sample counts", () => {
  const series: BootstrapTrendSeries = [
    [
      [0.1, 0.2, null],
      [0.2, null, null],
      [0.3, 0.3, null],
      [null, 0.25, null],
      [0.4, 0.4, null],
    ],
    [
      [0.15, 0.15, null, 0.25, 0.1, null, 0.001],
      [0.1, 0.3, null, 0.21, 0.2, null, 0.002],
      [0.35, 0.3, null, 0.41, 0.2, null, 0.003],
      [0.5, 0.2, null, 0.55, 0.15, null, null],
      [null, 0.1, null, 0.05, 0.05, null, null],
    ],
    Array.from({ length: 5 }, () => [0.02, 0.5, null]),
    Array.from({ length: 5 }, () => [0.1, 0, null]),
  ];
  const [reference, candidate, dominated, zeroDrawdown] =
    summarizeBootstrapTrend(series, 1, false);
  expect(candidate.medianCagr).toEqual({ value: 0.25, count: 4 });
  expect(candidate.p5Cagr.value).toBeCloseTo(0.1075);
  expect(candidate.medianDrawdown).toEqual({ value: 0.2, count: 5 });
  expect(candidate.p95Drawdown.value).toBeCloseTo(0.3);
  expect(candidate.calmar.value).toBeCloseTo((1 + 0.35 / 0.3) / 2);
  expect(candidate.calmar.count).toBe(4);
  expect(candidate.commissions).toEqual({ value: 0.002, count: 3 });
  expect(candidate.higherCagr).toEqual({ value: 2 / 3, count: 3 });
  expect(candidate.higherCagrLowerDrawdown).toEqual({ value: 1 / 2, count: 2 });
  expect(reference.higherCagr).toEqual({ value: 0, count: 4 });
  expect(dominated.pareto).toBe(false);
  expect(candidate.pareto).toBe(true);
  expect(zeroDrawdown.calmar).toEqual({ value: null, count: 0 });
  expect(reference.commissions).toEqual({ value: null, count: 0 });

  expect(summarizeBootstrapTrend(series, 2, false)[0].higherCagr).toEqual({
    value: 1 / 3,
    count: 3,
  });
  const gross = summarizeBootstrapTrend(series, 2, true)[1];
  expect(gross.medianCagr).toEqual({ value: 0.25, count: 5 });
  expect(gross.commissions).toEqual(candidate.commissions);
});
