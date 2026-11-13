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
it("plots all strategies with volatility on X and CAGR on Y", () => {
  render(<RebalancingView result={result} instruments={instruments} />);
  const calendar = plots.mock.calls.find(
    ([p]) =>
      p.title === "Правила ребалансировки: фактические доходность и риск",
  )![0];
  const markers = calendar.data.filter(
    (trace: { showlegend: boolean }) => trace.showlegend,
  );
  expect(markers).toHaveLength(21);
  const ordered = [...result.strategies].sort((a, b) =>
    a.label.localeCompare(b.label, "ru", { numeric: true }),
  );
  expect(markers.map((trace: { name: string }) => trace.name)).toEqual(
    ordered.map((s) => s.label),
  );
  expect(markers[0].x[0]).toBe(ordered[0].net.annualized_volatility);
  expect(markers[0].y[0]).toBe(ordered[0].net.annualized_cagr);
  expect(calendar.data[0].z).toBeUndefined();
  expect(
    calendar.data
      .filter((trace: { mode: string }) => trace.mode === "text+markers")
      .map((trace: { text: string[] }) => trace.text[0]),
  ).toEqual(expect.arrayContaining(["10%", "B&H", "год"]));
  const equity = plots.mock.calls.find(
    ([p]) => p.title === "Капитал по правилам ребалансировки",
  )![0];
  expect(equity.data.map((trace: { name: string }) => trace.name)).toEqual(
    ordered.map((s) => s.label),
  );
  const weights = plots.mock.calls.find(
    ([p]) => p.title === "Фактические веса портфеля",
  )![0];
  expect(weights.data[0].customdata).toEqual(weights.data[0].y);
  expect(weights.data[0].hovertemplate).toContain("%{customdata:.2%}");
  expect(
    weights.layout.shapes.some((s: { xref: string }) => s.xref === "x"),
  ).toBe(true);
  expect(
    weights.layout.shapes.every((s: { xref: string }) => s.xref === "x"),
  ).toBe(true);
  expect(screen.getByText("Веса перед ребалансировкой")).toBeInTheDocument();
  expect(screen.getByText("Комиссии, % капитала")).toBeInTheDocument();
  const table = screen.getByRole("table", {
    name: "Сравнение правил ребалансировки",
  });
  expect(within(table).getAllByRole("row")).toHaveLength(22);
  const annual = result.strategies.find((s) => s.id === "calendar_12")!;
  const annualRow = screen.getByText(/Каждый год · базовый/).closest("tr")!;
  expect(
    within(annualRow).getByText(
      `${(annual.total_commissions * 100).toFixed(4)}%`,
    ),
  ).toBeInTheDocument();
  expect(screen.getByText(/Каждый год · базовый/).closest("tr")).toHaveClass(
    "annual-baseline",
  );
  fireEvent.click(screen.getByRole("button", { name: "До комиссий" }));
  const updated = [...plots.mock.calls]
    .reverse()
    .find(
      ([p]) =>
        p.title === "Правила ребалансировки: фактические доходность и риск",
    )![0];
  expect(updated.data[0].x).not.toEqual(calendar.data[0].x);
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
