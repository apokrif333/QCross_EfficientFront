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
  default: ({ data }: { data: unknown }) => (
    <pre data-testid="plot-data">{JSON.stringify(data)}</pre>
  ),
}));

// Deliberately different model and historical risk: catches silent substitution.
const metrics: Metrics = {
  expected_return: 0.100966,
  volatility: 0.0959,
  historical_volatility: 0.0972,
  sharpe_ratio: 0.74,
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
  expect(hover).toContain("Sharpe: 0.740");
  expect(hover).not.toContain("9.72%");
});

it("shows passive and controlled paths at their realized arithmetic return and risk", () => {
  const calculation = saved();
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
      baseline: {
        net: baseline,
        gross: { ...baseline, annualized_volatility: 0.13 },
      },
      controlled: {
        net: controlled,
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
  for (const [label, stats] of [
    ["Портфель (пассивный)", baseline],
    ["Портфель (под управлением)", controlled],
  ] as const) {
    const trace = traces.find((p: { name: string }) => p.name === label);
    expect(trace.x).toEqual([stats.annualized_volatility]);
    expect(trace.y).toEqual([stats.annualized_arithmetic_return]);
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
  view.rerender(
    <FrontierView
      calculation={calculation}
      related={[calculation]}
      instruments={instruments}
      controlGross
    />,
  );
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
    expect(traces[0].hovertemplate[0]).toContain("Sharpe 0.740");
    const gmvLabel = compare ? "Sample · GMV" : "GMV";
    const button = screen.getByRole("button", { name: gmvLabel });
    const row = within(button.closest("tr")!);
    expect(row.getByText("9.59%", { exact: true })).toBeInTheDocument();
    expect(row.getByText("0.740", { exact: true })).toBeInTheDocument();
    expect(row.queryByText("9.72%", { exact: true })).not.toBeInTheDocument();
    await userEvent.setup().click(button);
    const detail = within(
      screen.getByRole("heading", { name: gmvLabel }).parentElement!,
    );
    expect(detail.getByText("9.59%", { exact: true })).toBeInTheDocument();
    expect(detail.getByText("0.740", { exact: true })).toBeInTheDocument();
    expect(
      detail.queryByText("9.72%", { exact: true }),
    ).not.toBeInTheDocument();
  },
);
