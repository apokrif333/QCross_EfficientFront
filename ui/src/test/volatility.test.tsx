import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { FrontierView } from "../components/Results";
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
