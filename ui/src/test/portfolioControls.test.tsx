import { useState } from "react";
import {
  render,
  screen,
  fireEvent,
  cleanup,
  within,
} from "@testing-library/react";
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
it("offers indicators only for assets with positive portfolio weights and no sensitivity selector", () => {
  const [a, b, c] = instruments;
  const rule = { indicator: "sma" as const, lookback_months: 12 };
  const request: ApiRequest = {
    ...DEFAULT_REQUEST,
    instrument_ids: [a.id, b.id, c.id],
    user_weights: { [a.id]: 0.6, [b.id]: 0.4, [c.id]: 0 },
    portfolio_controls: {
      mode: "trend",
      hedge_weights: { [a.id]: 1 },
      trend: {
        destination: "hedge",
        asset_rules: { [a.id]: rule, [c.id]: rule },
        sensitivity_asset: String(c.id),
      },
    },
  };
  const { rerender } = render(
    <PortfolioControls
      request={request}
      instruments={instruments}
      onChange={() => {}}
      locked={false}
    />,
  );
  expect(screen.getByRole("combobox", { name: a.ticker })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: b.ticker })).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: c.ticker })).toBeNull();
  expect(
    screen.queryByRole("combobox", { name: /чувствительности/i }),
  ).toBeNull();
  const changed = {
    ...request,
    user_weights: { [a.id]: 0.6, [b.id]: 0, [c.id]: 0.4 },
  };
  rerender(
    <PortfolioControls
      request={changed}
      instruments={instruments}
      onChange={() => {}}
      locked={false}
    />,
  );
  expect(screen.queryByRole("combobox", { name: b.ticker })).toBeNull();
  expect(screen.getByRole("combobox", { name: c.ticker })).toBeInTheDocument();
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
  expect(
    schema.safeParse({
      ...base,
      portfolio_controls: {
        mode: "downside_vol",
        hedge_weights: { BIL_REFERENCE: 1 },
        downside_vol: { target_annual: 0.09, lookback_months: 1 },
      },
    }).success,
  ).toBe(true);
  const controls = {
    mode: "trend",
    hedge_weights: { BIL_REFERENCE: 1 },
    trend: {
      destination: "hedge",
      asset_rules: { "1": { indicator: "momentum", lookback_months: 1 } },
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
it("plots a single labeled point per common period with drawdown on X and CAGR on Y", () => {
  const stats = {
    annualized_cagr: 0.1,
    annualized_volatility: 0.2,
    annualized_downside_volatility: 0.12,
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
  const points = Array.from({ length: 36 }, (_, i) => ({
    lookback_months: i + 1,
    net: {
      ...stats,
      annualized_cagr: 0.1 - i / 1000,
      maximum_drawdown: 0.2 + i / 1000,
    },
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
          sensitivity: { scope: "common_lookback", points },
        },
      }}
    />,
  );
  const sensitivity = () =>
    plots.mock.calls
      .filter(([p]) => p.title === "Чувствительность к периоду SMA/Momentum")
      .at(-1)![0];
  const p = sensitivity();
  expect(
    screen.getByText("Нисходящая годовая волатильность"),
  ).toBeInTheDocument();
  const weights = plots.mock.calls.find(
    ([p]) => p.title === "Управляемый портфель: фактические веса",
  )![0];
  expect(weights.data[0].hovertemplate).toContain(
    instruments.find((i) => i.id === 1)!.ticker,
  );
  expect(p.threeD).toBeUndefined();
  expect(p.layout.xaxis.title.text).toBe("Максимальная просадка портфеля");
  expect(p.layout.yaxis.title.text).toBe("CAGR портфеля");
  expect(p.data).toHaveLength(2);
  expect(p.data[1].name).toBe("Линейная регрессия");
  expect(p.data[1].x).toEqual([0.2, 0.23500000000000001]);
  expect(p.data[1].y[0]).toBeCloseTo(0.1);
  expect(p.data[1].y[1]).toBeCloseTo(0.065);
  expect(screen.getByText(/Корреляция Пирсона/)).toHaveTextContent(
    "r): -1.000 · R²: 1.000",
  );
  expect(p.data[0].mode).toBe("text+markers");
  expect(p.data[0].textposition).toBe("top center");
  expect(p.data[0].text).toEqual(points.map((p) => String(p.lookback_months)));
  expect(p.data[0].x).toEqual(points.map((p) => p.net.maximum_drawdown));
  expect(p.data[0].y).toEqual(points.map((p) => p.net.annualized_cagr));
  fireEvent.click(screen.getByRole("button", { name: "После комиссий" }));
  expect(sensitivity().data[0].x).toEqual(
    points.map((p) => p.gross.maximum_drawdown),
  );
  expect(sensitivity().data).toHaveLength(1);
  expect(screen.getByText(/Корреляция Пирсона/)).toHaveTextContent(
    "недостаточно различающихся данных",
  );
});

it("averages annual commission ratios including zero and partial years", () => {
  const stats = { annualized_cagr: 0.1 };
  const path = {
    net: stats,
    gross: stats,
    positions: [[0.99], [0.99], [1.98], [1.98], [1.98]],
    events: [
      {
        month_index: -1,
        commissions: 0.01,
        trades: [0.99],
        turnover: 0.99,
        kind: "initial_purchase",
      },
      {
        month_index: 0,
        commissions: 0.01,
        trades: [0.01],
        turnover: 0.01,
        kind: "rebalance",
      },
      {
        month_index: 1,
        commissions: 0.02,
        trades: [0.02],
        turnover: 0.01,
        kind: "rebalance",
      },
    ],
    net_wealth: [1, 1, 1, 1, 1],
    gross_wealth: [1, 1, 1, 1, 1],
    weight_history: [[1], [1], [1], [1]],
    signal_history: [],
  };
  render(
    <ControlResults
      instruments={instruments}
      result={{
        portfolio_controls: {
          config: { mode: "trend", trend: { asset_rules: {} } },
          months: ["2020-12", "2021-01", "2021-02", "2022-01"],
          assets: ["1"],
          baseline: path,
          controlled: path,
        },
      }}
    />,
  );
  const row = screen
    .getByText("Среднегодовые комиссии, % капитала")
    .closest("tr")!;
  expect(within(row).getAllByText("1.0000%")).toHaveLength(2);
});

it("plots drawdowns for both portfolios and follows the commission toggle", () => {
  const stats = { annualized_cagr: 0.1 };
  const base = {
    net: stats,
    gross: stats,
    net_wealth: [1, 1.2, 0.9, 1.3, 0],
    gross_wealth: [1, 1.2, 1.08, 1.3, 1.3],
    weight_history: [[1], [1], [1], [1]],
    signal_history: [],
    events: [],
  };
  const managed = {
    ...base,
    net_wealth: [1, 1.1, 1.1, 0.88, 1.2],
    gross_wealth: [1, 1.1, 1.1, 0.99, 1.2],
  };
  render(
    <ControlResults
      instruments={instruments}
      result={{
        portfolio_controls: {
          config: { mode: "trend", trend: { asset_rules: {} } },
          months: ["2020-01", "2020-02", "2020-03", "2020-04"],
          assets: ["1"],
          baseline: base,
          controlled: managed,
        },
      }}
    />,
  );
  const getDrawdowns = () =>
    plots.mock.calls
      .filter(([p]) => p.title === "Просадки: исходный и управляемый")
      .at(-1)![0];
  const net = getDrawdowns();
  expect(net.data.map((line: { name: string }) => line.name)).toEqual([
    "Исходный",
    "Управляемый",
  ]);
  expect(net.data[0].y[2]).toBeCloseTo(-0.25);
  expect(net.data[0].y[3]).toBe(0);
  expect(net.data[0].y[4]).toBe(-1);
  expect(net.data[1].y[3]).toBeCloseTo(-0.2);
  expect(net.data[0].x).toEqual([
    "2020-01",
    "2020-01",
    "2020-02",
    "2020-03",
    "2020-04",
  ]);
  expect(net.layout.yaxis.tickformat).toBe(".0%");
  fireEvent.click(screen.getByRole("button", { name: "После комиссий" }));
  const gross = getDrawdowns();
  expect(gross.data[0].y[2]).toBeCloseTo(-0.1);
  expect(gross.data[1].y[3]).toBeCloseTo(-0.1);
});
