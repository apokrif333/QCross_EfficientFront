import { useState } from "react";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import PortfolioControls from "../components/PortfolioControls";
import ControlResults from "../components/ControlResults";
import { DEFAULT_REQUEST, type ApiRequest } from "../types";
import { instruments } from "./fixtures";
import { requestSchemaFor } from "../lib/validation";

const plots = vi.hoisted(() => vi.fn());
vi.mock("../components/Plot", () => ({
  default: (p: unknown) => {
    plots(p);
    return <div />;
  },
}));
afterEach(() => {
  cleanup();
  plots.mockClear();
});
it("offers exactly three exclusive modes and a reusable BIL hedge", () => {
  function Editor() {
    const [request, set] = useState<ApiRequest>({
      ...DEFAULT_REQUEST,
      user_weights: { "1": 1 },
    });
    return (
      <PortfolioControls
        request={request}
        onChange={set}
        instruments={instruments}
        locked={false}
      />
    );
  }
  render(<Editor />);
  const mode = screen.getByRole("combobox", { name: "Режим" });
  expect(mode.querySelectorAll("option")).toHaveLength(3);
  fireEvent.change(mode, { target: { value: "trend" } });
  expect(screen.queryByText("Целевая downside volatility, %")).toBeNull();
  const hedge = screen.getByPlaceholderText("Тикер или ID · BIL_REFERENCE");
  fireEvent.change(hedge, { target: { value: "BIL_REFERENCE" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить в хедж" }));
  expect(screen.getByLabelText("Хедж BIL_REFERENCE, %")).toBeInTheDocument();
  fireEvent.change(mode, { target: { value: "downside_vol" } });
  expect(
    screen.getByText("Целевая downside volatility, %"),
  ).toBeInTheDocument();
});
it("validates hedge totals, lookbacks, modes and legacy absence", () => {
  const schema = requestSchemaFor("frontier");
  const base = { ...DEFAULT_REQUEST, user_weights: { "1": 1 } };
  expect(schema.safeParse(base).success).toBe(true);
  const controls = {
    mode: "trend",
    hedge_weights: { BIL_REFERENCE: 1 },
    trend: {
      destination: "hedge",
      asset_rules: { "1": { indicator: "momentum", lookback_months: 12 } },
    },
  };
  expect(
    schema.safeParse({ ...base, portfolio_controls: controls }).success,
  ).toBe(true);
  expect(
    schema.safeParse({
      ...base,
      portfolio_controls: {
        ...controls,
        hedge_weights: { BIL_REFERENCE: 0.9 },
      },
    }).success,
  ).toBe(false);
  expect(
    schema.safeParse({
      ...base,
      portfolio_controls: { ...controls, mode: "both" },
    }).success,
  ).toBe(false);
});
it("uses actual sensitivity points for the 3D plot", () => {
  const stats = {
    annualized_cagr: 0.1,
    annualized_volatility: 0.2,
    historical_sharpe_ratio: 0.3,
    maximum_drawdown: 0.1,
  };
  const path = {
    net: stats,
    gross: stats,
    net_wealth: [1, 1.01],
    gross_wealth: [1, 1.01],
    weight_history: [[1]],
    signal_history: [
      { alpha: 1, hedge_allocation: 0, signals: { "1": { active: true } } },
    ],
    events: [],
  };
  const points = Array.from({ length: 35 }, (_, i) => ({
    lookback_months: i + 2,
    net: { ...stats, annualized_cagr: i / 1000 },
    gross: stats,
    total_commissions: 0.001,
  }));
  render(
    <ControlResults
      instruments={instruments}
      result={{
        portfolio_controls: {
          config: { mode: "trend", trend: { asset_rules: { "1": {} } } },
          months: ["2000-01"],
          assets: ["1"],
          baseline: path,
          controlled: path,
          sensitivity: { asset: "1", points },
        },
      }}
    />,
  );
  const p = plots.mock.calls.find(([p]) => p.threeD)![0];
  expect(p.data[0].z).toEqual(points.map((p) => p.lookback_months));
  expect(p.data[0].x).toEqual(points.map((p) => p.net.annualized_cagr));
});
