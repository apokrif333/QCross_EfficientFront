import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import PairedComparisonView from "../components/PairedComparisonView";
import { requestSchemaFor } from "../lib/validation";
import type { PairedComparison } from "../types";
import { instruments, saved } from "./fixtures";
const plots = vi.hoisted(() => vi.fn());
vi.mock("../components/Plot", () => ({
  default: (props: unknown) => {
    plots(props);
    return <div />;
  },
}));
afterEach(() => {
  cleanup();
  plots.mockClear();
});
it("requires explicit weights and even paired bootstrap generations", () => {
  const request = {
    ...saved().request,
    objective: "max_return_at_user_risk",
    user_weights: null,
  };
  expect(requestSchemaFor("frontier").safeParse(request).success).toBe(false);
  request.user_weights = Object.fromEntries(
    request.instrument_ids.map((id, i) => [String(id), i === 0 ? 1 : 0]),
  ) as never;
  expect(requestSchemaFor("frontier").safeParse(request).success).toBe(true);
  expect(
    requestSchemaFor("bootstrap").safeParse({
      ...request,
      bootstrap_iterations: 5,
    }).success,
  ).toBe(false);
  expect(
    requestSchemaFor("bootstrap").safeParse({
      ...request,
      bootstrap_iterations: 500,
    }).success,
  ).toBe(true);
});
it("uses individual Validation observations for clouds and preserves separate counts", () => {
  const value: PairedComparison = {
    valid_count: 2,
    failed_count: 0,
    requested_comparisons: 2,
    generated_datasets: 4,
    horizon_months: 240,
    differences: {},
    win_frequencies: {},
    results: [],
    clouds: [1, 2].map((i) => ({
      pair: i,
      assets: { "1": { expected_return: 0.02 * i, volatility: 0.03 * i } },
      user: { expected_return: 0.04 * i, volatility: 0.05 * i },
      optimized: { expected_return: 0.06 * i, volatility: 0.07 * i },
    })),
    validation_frontier: [{ expected_return: 0.01, volatility: 0.02 }],
  };
  render(<PairedComparisonView value={value} instruments={instruments} />);
  const data = plots.mock.lastCall![0].data;
  expect(data[1].x).toEqual([0.05, 0.1]);
  expect(data[2].y).toEqual([0.06, 0.12]);
  expect(data[0].visible).toBe("legendonly");
  expect(data[1].type).toBe("histogram2dcontour");
  expect(data[3].name).toBe("Ex-post Validation Efficient Frontier");
  expect(screen.getByText(/4 генераций · 2 пар/)).toBeVisible();
});
