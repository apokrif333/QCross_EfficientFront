import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import PairedComparisonView from "../components/PairedComparisonView";
import PairedDifferenceHistogram from "../components/PairedDifferenceHistogram";
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
it("labels controlled CV and Bootstrap comparisons and their validation cloud", () => {
  const value: PairedComparison = {
    user_portfolio_mode: "controlled",
    user_weights: { "1": 1 },
    valid_count: 0,
    failed_count: 0,
    requested_comparisons: 0,
    differences: {
      delta_arithmetic_return: null,
      delta_terminal_wealth: null,
      delta_commissions: null,
      delta_capital_gains_tax: null,
      delta_total_costs: null,
      delta_annualized_total_costs: null,
    },
    win_frequencies: {},
    results: [],
    generated_datasets: 2,
    clouds: [
      {
        pair: 1,
        assets: {},
        user: { expected_return: 0.04, volatility: 0.05 },
        optimized: { expected_return: 0.06, volatility: 0.07 },
      },
    ],
  };
  const { rerender } = render(
    <PairedComparisonView value={value} instruments={instruments} />,
  );
  expect(screen.getByText("Δ конечного капитала на $1")).toBeVisible();
  expect(screen.getByText("Δ совокупных годовых издержек")).toBeVisible();
  for (const caption of [
    "Δ годовой арифметической доходности",
    "Δ комиссий на $1",
    "Δ уплаченного налога на $1",
    "Δ совокупных издержек на $1",
  ]) {
    expect(screen.queryByText(caption)).not.toBeInTheDocument();
  }
  rerender(
    <PairedComparisonView value={value} instruments={instruments} bootstrap />,
  );
  expect(
    screen.queryByText("Δ конечного капитала на $1"),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText(/Исходные целевые веса управляемого портфеля/),
  ).toBeVisible();
  expect(plots.mock.lastCall![0].data[0].name).toBe(
    "Ваш портфель под управлением (1)",
  );
  expect(plots.mock.lastCall![0].data[0].x).toEqual([0.05]);
  expect(
    screen.getByText(
      /фактическая годовая волатильность управляемой траектории на Train/,
    ),
  ).toBeVisible();
});
it("recomputes drawdown frequencies for saved results and excludes failed comparisons", () => {
  const value: PairedComparison = {
    valid_count: 3,
    failed_count: 1,
    requested_comparisons: 4,
    differences: {},
    win_frequencies: {
      lower_annualized_total_costs: { fraction: 0.25, denominator: 4 },
    },
    results: [
      {
        status: "optimal",
        optimized: {},
        differences: { delta_max_drawdown: -0.1, delta_cagr: 0.02 },
      },
      {
        status: "optimal",
        optimized: {},
        differences: { delta_max_drawdown: 0, delta_cagr: 0.05 },
      },
      {
        status: "optimal",
        optimized: {},
        differences: { delta_max_drawdown: 0.1, delta_cagr: 0.15 },
      },
      {
        status: "failed",
        optimized: null,
        differences: { delta_max_drawdown: -0.4, delta_cagr: 0.1 },
      },
    ],
  };
  render(<PairedComparisonView value={value} instruments={instruments} />);
  const metric = screen.getByText("Ниже просадки").closest("span")!;
  expect(within(metric).getByText("33.33%")).toBeVisible();
  expect(within(metric).getByText("3 сравнений")).toBeVisible();
  expect(screen.queryByText("Ниже годовые издержки")).not.toBeInTheDocument();
  const joint = screen
    .getByText("Выше CAGR при не большей просадке")
    .closest("span")!;
  expect(within(joint).getByText("66.67%")).toBeVisible();
  expect(within(joint).getByText("3 сравнений")).toBeVisible();
});

it("plots paired Validation differences including negative and zero outcomes", () => {
  const value: PairedComparison = {
    valid_count: 2,
    failed_count: 1,
    requested_comparisons: 3,
    generated_datasets: 6,
    horizon_months: 240,
    differences: {},
    win_frequencies: {},
    results: [
      {
        status: "optimal",
        optimized: { annualized_cagr: 0.1 },
        differences: {
          delta_cagr: -0.02,
          delta_volatility: 0.03,
          delta_max_drawdown: -0.15,
        },
      },
      {
        status: "optimal",
        optimized: { annualized_cagr: 0.1 },
        differences: {
          delta_cagr: 0,
          delta_volatility: -0.01,
          delta_max_drawdown: 0,
        },
      },
      {
        status: "failed",
        optimized: null,
        differences: {
          delta_cagr: 0.99,
          delta_volatility: 0.99,
          delta_max_drawdown: 0.99,
        },
      },
    ],
  };
  render(<PairedDifferenceHistogram value={value} />);
  expect(plots.mock.lastCall![0].data[0].x).toEqual([-2, 0]);
  expect(screen.getByText(/Успешных значений: 2 из 3/)).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "Разница волатильности" }),
  );
  expect(plots.mock.lastCall![0].data[0].x).toEqual([3, -1]);
  expect(plots.mock.lastCall![0].data[0].hovertemplate).toContain(
    "Число пар: %{y}",
  );
  fireEvent.click(screen.getByRole("button", { name: "Разница просадки" }));
  expect(plots.mock.lastCall![0].data[0].x).toEqual([-15, 0]);
  expect(plots.mock.lastCall![0].layout.xaxis.title.text).toBe(
    "Разница просадки, п.п.",
  );
  expect(
    screen.getByRole("button", { name: "Разница просадки" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    screen.getByRole("button", { name: "Разница волатильности" }),
  ).toHaveAttribute("aria-pressed", "false");
});
it("requests a new calculation for a legacy snapshot without paired values", () => {
  render(<PairedDifferenceHistogram />);
  expect(screen.getByText(/повторите Bootstrap/)).toBeVisible();
  expect(plots).not.toHaveBeenCalled();
});
it("requires explicit weights and accepts odd paired bootstrap generations", () => {
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
  ).toBe(true);
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

it("shows all 500 validation pairs and opens one log-scale wealth chart on demand", async () => {
  const path = (wealth: number) => {
    const bytes = Buffer.alloc(8);
    bytes.writeFloatLE(Math.log(wealth), 4);
    return bytes.toString("base64");
  };
  const value: PairedComparison = {
    user_weights: { "1": 1 },
    valid_count: 499,
    failed_count: 1,
    requested_comparisons: 500,
    generated_datasets: 1000,
    horizon_months: 1,
    differences: {},
    win_frequencies: {},
    results: Array.from({ length: 500 }, (_, index) => ({
      pair: index + 1,
      status: "optimal",
      weights: { "1": 0.25, "175": 0.75 },
      user: {
        annualized_cagr: 0.06,
        annualized_volatility: 0.2,
        maximum_drawdown: 0.3,
        historical_sharpe_ratio: 0.2,
      },
      optimized: {
        annualized_cagr: 0.12,
        annualized_volatility: 0.1,
        maximum_drawdown: 0.15,
        historical_sharpe_ratio: 0.8,
      },
      wealth_log_f32: {
        user: path(1.06),
        optimized: path(1.12 + index / 1000),
      },
    })),
  };
  value.results[499] = {
    pair: 500,
    status: "risk_infeasible",
    optimized: null,
  };
  render(<PairedComparisonView value={value} instruments={instruments} />);
  expect(
    screen.queryByRole("table", { name: "Все выборки Validation" }),
  ).toBeNull();
  fireEvent.click(screen.getByText("Все выборки Validation (500)"));
  const table = await screen.findByRole("table", {
    name: "Все выборки Validation",
  });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(501);
  expect(within(rows[1]).getByText("6.00% / 12.00%")).toBeVisible();
  expect(within(rows[1]).getByText("30.00% / 15.00%")).toBeVisible();
  expect(
    within(rows[1]).getByText(/Ваш Портфель \/ .*25\.00%.*75\.00%/),
  ).toBeVisible();
  expect(within(rows[500]).getByRole("button")).toBeDisabled();
  expect(plots).not.toHaveBeenCalled();
  fireEvent.click(within(rows[2]).getByRole("button"));
  const plot = plots.mock.lastCall![0];
  expect(plot.title).toBe("Капитал на Validation №2");
  expect(plot.layout.yaxis.type).toBe("log");
  expect(plot.data[0].y[0]).toBe(1);
  expect(plot.data[1].y[1]).toBeCloseTo(1.121);
});

it("sorts by optimizer minus user for every metric, keeping missing results last", async () => {
  const keys = [
    "annualized_cagr",
    "annualized_volatility",
    "maximum_drawdown",
    "historical_sharpe_ratio",
  ];
  const row = (pair: number, user: number, optimized: number) => ({
    pair,
    status: "optimal",
    weights: { [pair === 1 ? "175" : "1"]: 1 },
    user: {
      ...Object.fromEntries(keys.map((key) => [key, user])),
      annualized_commissions: 0.003,
      annualized_capital_gains_tax: 0.001,
      annualized_total_costs: 0.004,
    },
    optimized: Object.fromEntries(keys.map((key) => [key, optimized])),
  });
  const value: PairedComparison = {
    user_weights: { "1": 1 },
    valid_count: 3,
    failed_count: 1,
    requested_comparisons: 4,
    generated_datasets: 8,
    differences: {},
    win_frequencies: {},
    results: [
      row(1, 0.1, 0.2),
      row(2, 0.8, 0.75),
      row(3, 0.2, 0.5),
      { pair: 4, status: "failed", optimized: null },
    ],
  };
  render(<PairedComparisonView value={value} instruments={instruments} />);
  fireEvent.click(screen.getByText("Все выборки Validation (4)"));
  const table = await screen.findByRole("table", {
    name: "Все выборки Validation",
  });
  const order = () =>
    within(table)
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getByRole("rowheader").textContent!.charAt(0));
  expect(
    within(table).queryByRole("columnheader", {
      name: /Годовые комиссии|Годовой налог|Совокупные годовые издержки/,
    }),
  ).not.toBeInTheDocument();
  for (const label of ["CAGR", "Vol", "DD", "Sharpe"]) {
    fireEvent.click(within(table).getByRole("button", { name: label }));
    expect(order()).toEqual(["3", "1", "2", "4"]);
    expect(
      within(table).getByRole("columnheader", { name: label }),
    ).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(within(table).getByRole("button", { name: label }));
    expect(order()).toEqual(["2", "1", "3", "4"]);
    expect(
      within(table).getByRole("columnheader", { name: label }),
    ).toHaveAttribute("aria-sort", "ascending");
  }
  fireEvent.click(within(table).getByRole("button", { name: "Validation" }));
  expect(order()).toEqual(["4", "3", "2", "1"]);
  fireEvent.click(within(table).getByRole("button", { name: "Validation" }));
  expect(order()).toEqual(["1", "2", "3", "4"]);
  fireEvent.click(within(table).getByRole("button", { name: "Веса" }));
  expect(order()[3]).toBe("4");
  expect(
    within(table).getByRole("columnheader", { name: "Веса" }),
  ).toHaveAttribute("aria-sort", "descending");
});
