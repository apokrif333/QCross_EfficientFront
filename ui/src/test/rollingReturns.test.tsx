import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import FrontierRollingReturns from "../components/FrontierRollingReturns";
import { rollingReturns } from "../lib/rollingReturns";
import type { FrontierResult } from "../types";
import { saved } from "./fixtures";

vi.mock("../components/Plot", () => ({
  default: ({ title, data }: { title: string; data: unknown }) => (
    <div role="img" aria-label={title}>
      {JSON.stringify(data)}
    </div>
  ),
}));

const months = [2020, 2021].flatMap((year) =>
  Array.from(
    { length: 12 },
    (_, i) => `${year}-${String(i + 1).padStart(2, "0")}`,
  ),
);

it("annualizes NAV over each full calendar window and excludes zero starting NAV", () => {
  const wealth = [1, ...Array(11).fill(1), 2, ...Array(11).fill(0.5), 4];
  expect(rollingReturns(months, wealth, 2)).toEqual([
    { start: "2020-01", end: "2021-12", value: 1 },
  ]);
  const bankrupt = [1, ...Array(11).fill(1), ...Array(13).fill(0)];
  const windows = rollingReturns(months, bankrupt, 1);
  expect(windows).toHaveLength(12);
  expect(windows.every(({ value }) => value === -1)).toBe(true);
});

it("updates the bars, positive window counts and percentile thresholds from the chosen path", () => {
  const result = saved().result as FrontierResult;
  const selected = result.selected_portfolio;
  const wealth = [1, ...Array(11).fill(1), 2, ...Array(11).fill(0.5), 4];
  result.selected_portfolio = {
    ...selected,
    metrics: {
      ...selected.metrics!,
      wealth_history: { months, net: wealth, gross: wealth },
    },
  };
  result.portfolio_controls = {
    months,
    baseline: { net_wealth: wealth },
    controlled: { net_wealth: Array(25).fill(1) },
  };
  render(<FrontierRollingReturns result={result} />);
  expect(screen.getByText("Нулевые: 1 (100.00%)")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Портфель"), {
    target: { value: "optimized" },
  });
  fireEvent.change(screen.getByLabelText("Горизонт, лет"), {
    target: { value: "1" },
  });
  const bars = JSON.parse(
    screen.getByRole("img", { name: "Годовая скользящая доходность" })
      .textContent!,
  )[0];
  expect(bars.x).toHaveLength(13);
  expect(bars.customdata[0]).toBe("2020-01 — 2020-12");
  expect(screen.getByText("Положительные: 2 (15.38%)")).toBeInTheDocument();
  expect(screen.getByText("Отрицательные: 11 (84.62%)")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Верхний персентиль"), {
    target: { value: "95" },
  });
  expect(screen.getByText("P95")).toBeInTheDocument();
  expect(screen.getByText("Не ниже порога: 15.38%")).toBeInTheDocument();
});

it("compares annualized returns only for windows available in both portfolios", () => {
  const result = saved().result as FrontierResult;
  const passive = Array<number | null>(25).fill(1);
  passive[12] = passive[24] = 1.25;
  passive[13] = 0.8;
  const managed = Array<number | null>(25).fill(1);
  managed[1] = null;
  managed[14] = 1.1;
  result.portfolio_controls = {
    months,
    baseline: { net_wealth: passive },
    controlled: { net_wealth: managed },
  };
  const { rerender } = render(<FrontierRollingReturns result={result} />);
  fireEvent.change(screen.getByLabelText("Портфель"), {
    target: { value: "passive-vs-managed" },
  });
  fireEvent.change(screen.getByLabelText("Горизонт, лет"), {
    target: { value: "1" },
  });
  const [bars] = JSON.parse(
    screen.getByRole("img", { name: "Разница скользящей доходности" })
      .textContent!,
  );
  expect(bars.x).toHaveLength(12);
  expect(bars.x.slice(0, 2)).toEqual(["2020-12", "2021-02"]);
  expect(bars.y[0]).toBeCloseTo(25);
  expect(bars.y[1]).toBeCloseTo(-10);
  expect(bars.customdata[0]).toEqual(["2020-01 — 2020-12", 0.25, 0]);
  expect(screen.getByText("Пассивный выше: 1 (8.33%)")).toBeInTheDocument();
  expect(screen.getByText("Управляемый выше: 1 (8.33%)")).toBeInTheDocument();
  expect(screen.getByText("Одинаково: 10 (83.33%)")).toBeInTheDocument();
  expect(
    within(screen.getByText("Худшее окно").parentElement!).getByText(
      "-10.00 п. п.",
    ),
  ).toBeInTheDocument();

  rerender(
    <FrontierRollingReturns
      result={{ ...result, portfolio_controls: undefined }}
    />,
  );
  expect(
    screen.queryByRole("option", { name: "Пассивный − управляемый" }),
  ).not.toBeInTheDocument();
});
