import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import RebalancingView from "../components/RebalancingView";
import { rebalancingFixture } from "./rebalancingFixture";
import { createSnapshot, parseSnapshot } from "../lib/snapshot";
import { requestSchemaFor } from "../lib/validation";
import { instruments } from "./fixtures";
import type { Calculation } from "../types";
const plots = vi.hoisted(() => vi.fn());
vi.mock("../components/Plot", () => ({
  default: (props: unknown) => {
    plots(props);
    return <div />;
  },
}));
const result = rebalancingFixture();
afterEach(() => {
  cleanup();
  plots.mockClear();
});
it("plots ordered thresholds in 3D and leaves calendar policies in 2D", () => {
  render(<RebalancingView result={result} instruments={instruments} />);
  const chart = plots.mock.calls.find(
    ([p]) => p.title === "3D Rebalancing Robustness",
  )![0];
  expect(chart.threeD).toBe(true);
  expect(chart.data[0].type).toBe("scatter3d");
  expect(chart.data[0].z).toEqual([
    10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 150, 200,
  ]);
  const calendar = plots.mock.calls.find(
    ([p]) => p.title === "Календарные правила ребалансировки",
  )![0];
  expect(calendar.data[0].x).toHaveLength(7);
  expect(calendar.data[0].z).toBeUndefined();
  const table = screen.getByRole("table", {
    name: "Сравнение 20 правил ребалансировки",
  });
  expect(within(table).getAllByRole("row")).toHaveLength(21);
  expect(screen.getByText(/Каждый год · базовый/).closest("tr")).toHaveClass(
    "annual-baseline",
  );
  fireEvent.click(screen.getByRole("button", { name: "До комиссий" }));
  expect(plots.mock.calls.at(-4)![0].data[0].x).not.toEqual(chart.data[0].x);
});
it("round-trips actual simulation results and validates explicit user weights", () => {
  const request = result.reproducibility.api_request!;
  const calculation = {
    id: "rebalancing-test",
    kind: "rebalancing-optimization",
    request,
    result,
    origin: "demo",
    created_at: "test",
  } as Calculation;
  const snapshot = parseSnapshot(
    JSON.stringify(createSnapshot([calculation], instruments)),
  );
  expect(snapshot.calculations[0].result).toEqual(result);
  expect(
    requestSchemaFor("rebalancing-optimization").safeParse({
      ...request,
      user_weights: null,
    }).success,
  ).toBe(false);
  expect(
    requestSchemaFor("rebalancing-optimization").safeParse({
      ...request,
      objective: "target_return",
      target_return: null,
    }).success,
  ).toBe(true);
});
