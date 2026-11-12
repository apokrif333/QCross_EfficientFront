import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  RollingEfficiencyView,
  CVView,
  BootstrapView,
  ResampledView,
} from "../components/Results";
import {
  compatible,
  effectiveRequest,
  createSnapshot,
  parseSnapshot,
  stable,
} from "../lib/snapshot";
import { requestSchemaFor } from "../lib/validation";
import type {
  BootstrapResult,
  CVResult,
  EfficiencyDistribution,
  RollingEfficiencyResult,
  Calculation,
  ResampledResult,
} from "../types";
import { saved, instruments } from "./fixtures";

const plots = vi.hoisted(() => vi.fn());
vi.mock("../components/Plot", () => ({
  default: (props: unknown) => {
    plots(props);
    return <div data-testid="plot" />;
  },
}));
afterEach(() => {
  cleanup();
  plots.mockClear();
});

const stats = { mean: 0.02, median: 0.02, p5: 0.01, p95: 0.03, maximum: 0.04 };
const distance = {
  status: "optimal",
  return_shortfall: 0.02,
  excess_volatility: 0.01,
  user_expected_return: 0.08,
  user_volatility: 0.1,
  diagnostics: {},
};
function rolling(): Calculation {
  const source = saved();
  const user_weights = Object.fromEntries(
    source.result.assets.map((a) => [a, 1 / source.result.assets.length]),
  );
  const request = {
    ...source.request,
    user_weights,
    include_efficiency_robustness: true,
    rolling_window_months: 120,
    rolling_step_months: 12,
  };
  const result: RollingEfficiencyResult = {
    ...source.result,
    status: "incomplete",
    methodology: "rolling_fixed_portfolio_efficiency_distance",
    user_weights,
    window_months: 120,
    step_months: 12,
    requested_cv_folds: 4,
    effective_cv_folds: 2,
    return_basis: "real",
    risk_free_source: "fixed_annual",
    windows: [
      {
        ...distance,
        window_number: 1,
        start_month: "2000-01",
        end_month: "2009-12",
        observations: 120,
        effective_cv_folds: 2,
        covariance_parameters: {},
        covariance_diagnostics: {},
      },
      {
        ...distance,
        status: "solver_failed",
        return_shortfall: null,
        excess_volatility: null,
        window_number: 2,
        start_month: "2001-01",
        end_month: "2010-12",
        observations: 120,
        effective_cv_folds: 2,
        covariance_parameters: {},
        covariance_diagnostics: {},
      },
    ],
    summary: {
      valid_count: 1,
      failed_count: 1,
      return_shortfall: stats,
      excess_volatility: stats,
    },
    reproducibility: { ...source.result.reproducibility, api_request: request },
  };
  return { ...source, kind: "rolling-efficiency", request, result };
}

it("renders rolling distances in percentage points and leaves gaps at failed windows", () => {
  const result = rolling().result as RollingEfficiencyResult;
  result.windows[1].diagnostics = {
    reason:
      "Analytical nonlinear shrinkage input is singular or nearly singular.",
  };
  render(<RollingEfficiencyView result={result} instruments={instruments} />);
  const chart = plots.mock.calls[0][0];
  expect(chart.data[0].x).toEqual(["2009-12", "2010-12"]);
  expect(chart.data[0].y).toEqual([2, null]);
  expect(chart.data[0].connectgaps).toBe(false);
  expect(chart.data[0].customdata[0]).toEqual(["2000-01", "2009-12"]);
  expect(chart.data[0].hovertemplate).toContain("Период:");
  expect(chart.data[0].hovertemplate).toContain("Значение: %{y:.2f} п.п.");
  expect(chart.layout.xaxis.title.text).toBe("Конец 10-летнего окна");
  expect(
    screen.getByRole("heading", { name: "Упущенная годовая доходность, п.п." }),
  ).toBeVisible();
  expect(screen.getByText(/при волатильности не выше/)).toBeVisible();
  expect(screen.getAllByTestId("plot")).toHaveLength(1);
  const shortfallButton = screen.getByRole("button", {
    name: "Упущенная годовая доходность",
  });
  const riskButton = screen.getByRole("button", {
    name: "Избыточная годовая волатильность",
  });
  expect(shortfallButton).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(riskButton);
  expect(riskButton).toHaveAttribute("aria-pressed", "true");
  expect(shortfallButton).toHaveAttribute("aria-pressed", "false");
  expect(screen.getAllByTestId("plot")).toHaveLength(1);
  expect(plots.mock.lastCall![0].data[0].y).toEqual([1, null]);
  expect(screen.getByText(/при доходности не ниже/)).toBeVisible();
  fireEvent.click(shortfallButton);
  expect(plots.mock.lastCall![0].data[0].y).toEqual([2, null]);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(
    screen.getAllByText(/Упущенная годовая доходность, п.п./).length,
  ).toBeGreaterThan(0);
  expect(
    screen.getByText(/Ваш портфель \(веса одинаковы во всех окнах\)/),
  ).toHaveTextContent(instruments[0].ticker);
  expect(screen.getAllByText(/mean 2.00 п.п.; median 2.00/)).toHaveLength(2);
  expect(screen.getByText(/Real USD/)).toBeVisible();
  const headings = screen
    .getAllByRole("heading")
    .map((heading) => heading.textContent);
  expect(
    headings.findIndex((name) => name?.includes("Скользящие 10-летние окна")),
  ).toBeLessThan(headings.indexOf("Упущенная годовая доходность, п.п."));
  expect(headings.some((name) => name?.includes("Rolling"))).toBe(false);
  const summary = screen.getAllByText(/mean 2.00 п.п.; median 2.00/)[0];
  expect(
    summary.compareDocumentPosition(shortfallButton) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it("exposes the actual failure reason for a validation fold", () => {
  const r = saved("cross-validation", "cross-validation.json")
    .result as CVResult;
  r.folds[0].efficiency_robustness = {
    ...distance,
    status: "failed",
    user_expected_return: null,
    user_volatility: null,
    return_shortfall: null,
    excess_volatility: null,
    diagnostics: {
      reason:
        "Analytical nonlinear shrinkage input is singular or nearly singular.",
    },
  };
  r.folds[0].paired_comparison = {
    status: "failed",
    diagnostics: {
      reason:
        "Analytical nonlinear shrinkage input is singular or nearly singular.",
    },
  };
  r.efficiency_robustness = {
    user_weights: { [r.assets[0]]: 1 },
    valid_count: 3,
    failed_count: 1,
    return_shortfall: stats,
    excess_volatility: stats,
  };
  render(<CVView result={r} instruments={instruments} />);
  expect(screen.getByTitle(/Матрица ковариации вырождена/)).toHaveTextContent(
    "failed",
  );
});

it("shows CV summaries above the existing table and adds one user-portfolio column", () => {
  const r = saved("cross-validation", "cross-validation.json")
    .result as CVResult;
  r.folds = r.folds.map((fold) => ({
    ...fold,
    efficiency_robustness: distance,
    training_metrics: fold.training_metrics && {
      ...fold.training_metrics,
      historical_cagr: 0.11,
      historical_volatility:
        0.1 *
        Math.sqrt(
          (fold.training.observations - 1) / fold.training.observations,
        ),
    },
    validation_metrics: fold.validation_metrics && {
      ...fold.validation_metrics,
      historical_cagr: 0.1084,
      realized_annual_arithmetic_return: 0.1144,
      realized_annual_volatility: 0.1509,
    },
    paired_comparison: {
      status: "optimal",
      user: { annualized_cagr: 0.1774, annualized_volatility: 0.1493 },
    },
  }));
  r.efficiency_robustness = {
    user_weights: { [r.assets[0]]: 1 },
    valid_count: 4,
    failed_count: 0,
    return_shortfall: stats,
    excess_volatility: stats,
  };
  render(<CVView result={r} instruments={instruments} />);
  fireEvent.click(
    screen.getByText(
      "Ретроспективные расстояния до границы (дополнительная диагностика)",
    ),
  );
  const column = screen.getByRole("columnheader", {
    name: "Your Port on Valid CAGR / historical vol",
  });
  expect(column).toBeVisible();
  const summary = screen.getByText(
    "Упущенная годовая доходность (указанного портфеля), п.п.",
  );
  expect(summary).toBeVisible();
  expect(
    screen.getByText(
      "Избыточная годовая волатильность (указанного портфеля), п.п.",
    ),
  ).toBeVisible();
  expect(
    summary.compareDocumentPosition(column) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getAllByText(/mean 2.00 п.п.; median 2.00/)).toHaveLength(2);
  expect(
    screen.queryByRole("heading", {
      name: /Cross-validation · ex-post границы/,
    }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "Окно / фолд" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "Статус" }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByText("17.74% / 14.93%")).toHaveLength(4);
  expect(screen.getAllByText("10.84% / 15.09%")).toHaveLength(4);
  expect(screen.getAllByText("11.00% / 10.00%")).toHaveLength(4);
  expect(
    screen.queryByText("Результаты сравнений и веса на Train"),
  ).not.toBeInTheDocument();
  const headings = screen
    .getAllByRole("heading")
    .map((heading) => heading.textContent);
  const stability = headings.indexOf("Устойчивость весов");
  const comparison = headings.indexOf("Сравнение на полной выборке");
  expect(stability).toBeGreaterThanOrEqual(0);
  expect(stability).toBeLessThan(comparison);
});

it("hides user-portfolio CV information when no allocation is specified", () => {
  const r = saved("cross-validation", "cross-validation.json")
    .result as CVResult;
  r.efficiency_robustness = null;
  r.folds[0].efficiency_robustness = distance;
  render(<CVView result={r} instruments={instruments} />);
  expect(
    screen.queryByText(
      "Упущенная годовая доходность (указанного портфеля), п.п.",
    ),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(
      "Избыточная годовая волатильность (указанного портфеля), п.п.",
    ),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", {
      name: "Your Port on Valid CAGR / historical vol",
    }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Fold" })).toBeVisible();
});

it("uses source tickers for rolling weights when the catalog is unavailable", () => {
  const result = rolling().result as RollingEfficiencyResult;
  render(<RollingEfficiencyView result={result} />);
  expect(
    screen.getByText(/Ваш портфель \(веса одинаковы во всех окнах\)/),
  ).toHaveTextContent(result.source_metadata[0].ticker);
});

it("shows the user portfolio on the original sample and as a bootstrap average", () => {
  const result = saved("resampled-frontier", "resampled-frontier.json")
    .result as ResampledResult;
  result.user_portfolio = result.frontier[0];
  result.user_sample_average = {
    expected_return: 0.08,
    volatility: 0.12,
    successful_samples: 10,
  };
  render(<ResampledView result={result} instruments={instruments} />);
  const data = plots.mock.lastCall![0].data;
  expect(
    data.find(
      (p: { name: string }) => p.name === "Ваш портфель — исходная выборка",
    ),
  ).toBeDefined();
  const average = data.find(
    (p: { name: string }) => p.name === "Ваш портфель — среднее Bootstrap",
  );
  expect(average.x).toEqual([0.12]);
  expect(average.y).toEqual([0.08]);
  expect(average.hovertemplate).toContain("Успешных выборок: 10");
  expect(
    screen.getByText(/средняя точка — среднее доходности и волатильности/),
  ).toBeVisible();
});

it("shows all fixed-portfolio distributions with their full horizon and preserves snapshots", () => {
  const calculation = saved("bootstrap", "bootstrap.json");
  const r = calculation.result as BootstrapResult;
  r.efficiency_robustness = {
    user_weights: { [r.assets[0]]: 1 },
    valid_count: 2,
    failed_count: 0,
    return_shortfall: stats,
    excess_volatility: stats,
    horizon: {
      months: 240,
      years: 20,
      initial_wealth: 1,
      sample_length_policy: "full_selected_return_panel",
      rebalancing: "monthly_fixed_weights",
    },
    distributions: Object.fromEntries(
      [
        "annualized_cagr",
        "annualized_arithmetic_return",
        "annualized_volatility",
        "maximum_drawdown",
        "historical_sharpe_ratio",
        "terminal_wealth",
        "return_shortfall",
        "excess_volatility",
      ].map((key) => [
        key,
        {
          mean:
            key === "terminal_wealth"
              ? 1.5
              : key === "annualized_cagr"
                ? -0.02
                : 0.02,
          median: 0.02,
          p5: 0.01,
          p95: 0.03,
          minimum: key === "annualized_cagr" ? -0.03 : 0.01,
          maximum: 0.04,
          valid_count: 2,
          unavailable_count: 0,
          horizon_months: 240,
          horizon_years: 20,
          annualized: key !== "terminal_wealth" && key !== "maximum_drawdown",
          units:
            key === "terminal_wealth"
              ? "wealth_from_one_usd"
              : "decimal_fraction",
        },
      ]),
    ),
  };
  render(<BootstrapView result={r} instruments={instruments} />);
  const table = screen.getByRole("table", {
    name: "Распределения показателей фиксированного портфеля",
  });
  expect(within(table).getAllByRole("row")).toHaveLength(7);
  expect(within(table).getAllByText("240 мес. (20.00 лет)")).toHaveLength(6);
  expect(
    within(table).queryByText("Упущенная годовая доходность, п.п."),
  ).not.toBeInTheDocument();
  expect(
    within(table).queryByText("Избыточная годовая волатильность, п.п."),
  ).not.toBeInTheDocument();
  expect(within(table).getByText("$1.500")).toBeVisible();
  expect(within(table).getByText("-2.00%")).toBeVisible();
  expect(
    screen.getByText(/не являются калиброванными вероятностями/),
  ).toBeVisible();
  const restored = parseSnapshot(
    JSON.stringify(createSnapshot([calculation], instruments)),
  );
  expect(
    (restored.calculations[0].result as BootstrapResult).efficiency_robustness
      ?.distributions?.annualized_cagr.minimum,
  ).toBe(-0.03);
});

it("round-trips rolling snapshots and rejects nonfinite distance outputs", () => {
  const calculation = rolling();
  expect(
    parseSnapshot(JSON.stringify(createSnapshot([calculation], instruments)))
      .calculations[0].request.user_weights,
  ).toEqual(calculation.request.user_weights);
  (calculation.result as RollingEfficiencyResult).windows[0].return_shortfall =
    -0.01;
  expect(() =>
    parseSnapshot(JSON.stringify(createSnapshot([calculation], instruments))),
  ).toThrow();
});

it("requires a complete explicit allocation for the standalone rolling request", () => {
  const request = rolling().request;
  expect(
    requestSchemaFor("rolling-efficiency").safeParse(request).success,
  ).toBe(true);
  for (const user_weights of [
    null,
    {},
    { [String(request.instrument_ids[0])]: 0.5 },
  ])
    expect(
      requestSchemaFor("rolling-efficiency").safeParse({
        ...request,
        user_weights,
      }).success,
    ).toBe(false);
});

it("treats omitted new defaults as equivalent without rewriting legacy snapshots", () => {
  const legacy = saved();
  const explicit = structuredClone(legacy);
  explicit.request = {
    ...legacy.request,
    include_efficiency_robustness: true,
    rolling_window_months: 120,
    rolling_step_months: 12,
  };
  expect(compatible(legacy, explicit)).toBe(true);
  expect(stable(effectiveRequest(legacy.request))).toBe(
    stable(effectiveRequest(explicit.request)),
  );
  expect(legacy.request.rolling_window_months).toBeUndefined();
  explicit.request.include_efficiency_robustness = false;
  expect(compatible(legacy, explicit)).toBe(false);
});
