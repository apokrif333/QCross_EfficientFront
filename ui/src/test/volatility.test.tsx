import { render, screen, within, fireEvent } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { FrontierView } from "../components/Results";
import ControlResults from "../components/ControlResults";
import { displayedVolatility, portfolioHover } from "../lib/display";
import type { FrontierResult, Metrics } from "../types";
import { instruments, saved } from "./fixtures";

vi.mock("../components/Plot", () => ({
  default: ({
    data,
    layout,
  }: {
    data: unknown;
    layout?: { yaxis?: { type?: string } };
  }) => (
    <pre data-testid="plot-data" data-y-scale={layout?.yaxis?.type}>
      {JSON.stringify(data)}
    </pre>
  ),
}));

// Deliberately different model and historical risk: catches silent substitution.
const metrics: Metrics = {
  expected_return: 0.100966,
  volatility: 0.0959,
  historical_volatility: 0.0972,
  sharpe_ratio: 0.74,
  historical_sharpe_ratio: 0.61,
  historical_cagr: 0.08,
  historical_max_drawdown: 0.2,
};

it("uses model volatility without historical fallback, including zero risk", () => {
  expect(displayedVolatility(metrics)).toBe(0.0959);
  expect(displayedVolatility({ ...metrics, volatility: 0 })).toBe(0);
  expect(displayedVolatility(undefined)).toBeUndefined();
  expect(
    displayedVolatility({ historical_volatility: 0.0972 } as Metrics),
  ).toBeUndefined();
  const p = (saved().result as FrontierResult).gmv;
  const hover = portfolioHover({ ...p, metrics }, instruments);
  expect(hover).toContain("Волатильность: 9.59%");
  expect(hover).toContain("Sharpe: 0.610");
  expect(hover).not.toContain("0.740");
  expect(hover).not.toContain("9.72%");
});

it("switches passive Frontier histories and applies log scale only to capital", async () => {
  const calculation = saved();
  const result = calculation.result as FrontierResult;
  result.user_portfolio = {
    ...result.gmv,
    metrics: {
      ...metrics,
      wealth_history: {
        months: ["2000-01", "2000-02"],
        net: [1, 1.25, 1],
        gross: [1, 1.25, 1.125],
      },
    },
  };
  render(
    <FrontierView
      calculation={calculation}
      related={[calculation]}
      instruments={instruments}
    />,
  );
  const paths = () =>
    screen
      .getAllByTestId("plot-data")
      .slice(1)
      .map((plot) => JSON.parse(plot.textContent!)[0].y as number[]);
  let [wealth, drawdown] = paths();
  expect(wealth).toEqual([1, 1.25, 1]);
  expect(drawdown[2]).toBeCloseTo(-0.2);
  await userEvent.click(screen.getByRole("button", { name: "После комиссий" }));
  [wealth, drawdown] = paths();
  expect(wealth).toEqual([1, 1.25, 1.125]);
  expect(drawdown[2]).toBeCloseTo(-0.1);
  const [, capitalPlot, drawdownPlot] = screen.getAllByTestId("plot-data");
  expect(capitalPlot).toHaveAttribute("data-y-scale", "linear");
  await userEvent.click(
    screen.getByRole("button", { name: "Логарифмическая" }),
  );
  expect(capitalPlot).toHaveAttribute("data-y-scale", "log");
  expect(drawdownPlot).not.toHaveAttribute("data-y-scale", "log");
  expect(paths()).toEqual([wealth, drawdown]);
  await userEvent.click(screen.getByRole("button", { name: "Обычная" }));
  expect(capitalPlot).toHaveAttribute("data-y-scale", "linear");
});

it("shows passive and controlled paths at their realized arithmetic return and risk", () => {
  const calculation = saved();
  (calculation.result as FrontierResult).gmv.metrics = {
    ...metrics,
    rolling_3y: { positive: 1098, total: 1232 },
  };
  const baseline = {
    annualized_arithmetic_return: 0.08,
    annualized_volatility: 0.12,
    annualized_cagr: 0.07,
    maximum_drawdown: 0.2,
    historical_sharpe_ratio: 0.4,
  };
  const controlled = {
    ...baseline,
    annualized_arithmetic_return: 0.09,
    annualized_volatility: 0.1,
  };
  Object.assign(calculation.result, {
    portfolio_controls: {
      months: Array.from({ length: 61 }, (_, i) => String(i)),
      baseline: {
        net: baseline,
        net_wealth: [...Array(60).fill(1), 2, 0.5],
        gross: {
          ...baseline,
          annualized_volatility: 0.13,
          maximum_drawdown: 0.25,
        },
      },
      controlled: {
        net: controlled,
        net_wealth: [...Array(60).fill(1), 2, 2],
        gross: { ...controlled, annualized_volatility: 0.11 },
      },
    },
  });
  const view = render(
    <FrontierView
      calculation={calculation}
      related={[calculation]}
      instruments={instruments}
    />,
  );
  const traces = JSON.parse(screen.getByTestId("plot-data").textContent!);
  const gmv = traces.find((p: { name: string }) => p.name === "GMV")
    .hovertemplate[0];
  expect(gmv).toContain("Положительные (3 года): 1098 (89.12%)");
  expect(gmv).toContain("Sharpe 0.610");
  expect(gmv).not.toContain("0.740");
  expect(
    screen.getByRole("columnheader", { name: /^Sharpe$/ }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "Historical Sharpe" }),
  ).toBeNull();
  for (const [label, stats] of [
    ["Портфель (пассивный)", baseline],
    ["Портфель (под управлением)", controlled],
  ] as const) {
    const trace = traces.find((p: { name: string }) => p.name === label);
    expect(trace.x).toEqual([stats.annualized_volatility]);
    expect(trace.y).toEqual([stats.annualized_arithmetic_return]);
    expect(trace.hovertemplate[0]).toContain(
      "Максимальная просадка (DD) 20.000%",
    );
    const row = within(
      screen.getByRole("button", { name: label }).closest("tr")!,
    );
    expect(
      row.getByText(`${(stats.annualized_volatility * 100).toFixed(3)}%`),
    ).toBeInTheDocument();
  }
  expect(
    screen.queryByRole("button", { name: /^Ваш портфель$/ }),
  ).not.toBeInTheDocument();
  const historical = traces.find(
    (p: { name: string }) => p.name === "Портфель (пассивный)",
  );
  expect(historical.hovertemplate[0]).not.toContain("Estimated Sharpe");
  expect(historical.hovertemplate[0]).toContain(
    "Положительные (3 года): 1 (3.85%)",
  );
  view.rerender(
    <FrontierView
      calculation={calculation}
      related={[calculation]}
      instruments={instruments}
      controlGross
    />,
  );
  const grossTraces = JSON.parse(screen.getByTestId("plot-data").textContent!);
  expect(
    grossTraces.find((p: { name: string }) => p.name === "Портфель (пассивный)")
      .hovertemplate[0],
  ).toContain("Максимальная просадка (DD) 25.000%");
  expect(
    within(
      screen
        .getByRole("button", {
          name: "Портфель (пассивный)",
        })
        .closest("tr")!,
    ).getByText("13.000%"),
  ).toBeInTheDocument();
});

it("keeps Frontier and diagnostics on the same gross or net historical metrics", () => {
  const calculation = saved();
  const stats = (ret: number, vol: number) => ({
    annualized_arithmetic_return: ret,
    annualized_volatility: vol,
    annualized_cagr: ret,
    maximum_drawdown: 0.2,
    historical_sharpe_ratio: 0.4,
  });
  const path = (
    net: ReturnType<typeof stats>,
    gross: ReturnType<typeof stats>,
  ) => ({
    net,
    gross,
    net_wealth: [1, 1],
    gross_wealth: [1, 1],
    weight_history: [[1]],
    signal_history: [{ alpha: 1, hedge_allocation: 0, signals: {} }],
    events: [],
  });
  Object.assign(calculation.result, {
    portfolio_controls: {
      baseline: path(stats(0.08, 0.12), stats(0.085, 0.125)),
      controlled: path(stats(0.09, 0.1), stats(0.095, 0.105)),
      months: ["2000-01"],
      assets: ["1"],
      config: { mode: "trend", trend: { asset_rules: {} } },
    },
  });
  function View() {
    const [gross, setGross] = useState(false);
    return (
      <>
        <FrontierView
          calculation={calculation}
          related={[calculation]}
          instruments={instruments}
          controlGross={gross}
        />
        <ControlResults
          result={calculation.result}
          instruments={instruments}
          gross={gross}
          onGrossChange={setGross}
        />
      </>
    );
  }
  render(<View />);
  const frontier = screen
    .getByRole("button", { name: "Портфель (пассивный)" })
    .closest("tr")!;
  expect(within(frontier).getByText("12.000%")).toBeInTheDocument();
  const diagnostics = screen
    .getByText("Историческая годовая волатильность")
    .closest("tr")!;
  expect(within(diagnostics).getByText("12.000%")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "После комиссий" }));
  expect(within(frontier).getByText("12.500%")).toBeInTheDocument();
  expect(within(diagnostics).getByText("12.500%")).toBeInTheDocument();
});

it.each([false, true])(
  "keeps plot X, hover, portfolio table and point detail consistent (Compare=%s)",
  async (compare) => {
    const calculation = saved();
    const result = calculation.result as FrontierResult;
    result.gmv.metrics = metrics;
    result.frontier[0].metrics = metrics;
    if (result.asset_portfolios) {
      Object.values(result.asset_portfolios)[0].metrics = metrics;
    }
    render(
      <FrontierView
        calculation={calculation}
        related={[calculation]}
        instruments={instruments}
        compare={compare}
      />,
    );
    const traces = JSON.parse(screen.getByTestId("plot-data").textContent!);
    expect(traces[0].x[0]).toBe(metrics.volatility);
    expect(traces[0].hovertemplate[0]).toContain("Волатильность 9.59%");
    expect(traces[0].hovertemplate[0]).toContain(
      "Максимальная просадка (DD) 20.00%",
    );
    expect(
      traces.find((trace: { name: string }) => trace.name === "Активы")
        .hovertemplate[0],
    ).toContain("Максимальная просадка (DD) 20.00%");
    expect(traces[0].hovertemplate[0]).toContain("Sharpe 0.610");
    const gmvLabel = compare ? "Sample · GMV" : "GMV";
    const button = screen.getByRole("button", { name: gmvLabel });
    const row = within(button.closest("tr")!);
    expect(row.getByText("9.59%", { exact: true })).toBeInTheDocument();
    expect(row.getByText("0.610", { exact: true })).toBeInTheDocument();
    expect(row.queryByText("9.72%", { exact: true })).not.toBeInTheDocument();
    await userEvent.setup().click(button);
    const detail = within(
      screen.getByRole("heading", { name: gmvLabel }).parentElement!,
    );
    expect(detail.getByText("9.59%", { exact: true })).toBeInTheDocument();
    expect(detail.getByText("0.610", { exact: true })).toBeInTheDocument();
    expect(
      detail.queryByText("9.72%", { exact: true }),
    ).not.toBeInTheDocument();
  },
);
