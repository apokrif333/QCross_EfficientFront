import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import type { Job } from "../src/types";
import { ANNUAL_TAX_METHODOLOGY } from "../src/lib/snapshot";

test("Bootstrap trend densities are calculated on demand and toggle by period, risk and asset", async ({
  page,
}) => {
  const manifest = JSON.parse(
    await readFile("public/demo/manifest.json", "utf8"),
  );
  const result = JSON.parse(
    await readFile("public/demo/bootstrap.json", "utf8"),
  );
  const series = (shift: number) =>
    Array.from({ length: 36 }, (_, period) =>
      Array.from({ length: 500 }, (_, draw) => [
        shift +
          0.08 +
          (draw % 2 ? 0.02 : -0.02) *
            Math.sin(Math.floor(draw / 2) / 2 + period / 4),
        0.16 + 0.04 * Math.cos(draw / 3 + period / 3),
        0.29 + 0.04 * Math.cos(draw / 3 + period / 3),
        shift +
          0.18 +
          (draw % 2 ? 0.02 : -0.02) *
            Math.sin(Math.floor(draw / 2) / 2 + period / 4),
        0.14 + 0.04 * Math.cos(draw / 3 + period / 3),
        0.28 + 0.04 * Math.cos(draw / 3 + period / 3),
        0.001 + (period + 1) / 100000,
        0.002,
        0.003 + (period + 1) / 100000,
        0.02,
        0.04,
        0.06,
        0,
        0.5 + period / 100,
        0.8 + period / 100,
        0.7 + period / 100,
        1.1 + period / 100,
      ]),
    );
  const asset = result.assets[0];
  const trend_sensitivity = {
    iterations: 500,
    commission_basis: "pretrade_nav",
    tax_methodology: ANNUAL_TAX_METHODOLOGY,
    portfolio: series(0),
    by_asset: { [asset]: series(0.3) },
  };
  const request = {
    ...result.reproducibility.api_request,
    bootstrap_iterations: 500,
    capital_gains_tax_rate: 0.1,
    user_weights: { [asset]: 1 },
    portfolio_controls: {
      mode: "trend",
      hedge_weights: { [result.assets[1]]: 1 },
      trend: {
        destination: "hedge",
        asset_rules: {
          [asset]: { indicator: "momentum", lookback_months: 12 },
        },
      },
    },
  };
  result.reproducibility.api_request = request;
  result.reproducibility.tax_methodology = ANNUAL_TAX_METHODOLOGY;
  const bootstrapJob: Job = {
    job_id: "bootstrap-source",
    kind: "bootstrap",
    status: "running",
    timeout_seconds: 30,
    elapsed_seconds: 0,
    error: null,
    status_url: "/api/v1/analytics/jobs/bootstrap-source",
    result_url: "/api/v1/analytics/jobs/bootstrap-source/result",
  };
  const completed: Job = {
    ...bootstrapJob,
    status: "completed",
    bootstrap_trend_sensitivity_available: true,
  };
  const sensitivityJob: Job = {
    ...bootstrapJob,
    job_id: "bootstrap-sensitivity",
    kind: "bootstrap-trend-sensitivity",
    elapsed_seconds: 34.5,
    timeout_seconds: 300,
    status_url: "/api/v1/analytics/jobs/bootstrap-sensitivity",
    result_url: "/api/v1/analytics/jobs/bootstrap-sensitivity/result",
  };
  let submissions = 0;
  let sensitivityDone = false;
  await page.route("**/api/v1/analytics/catalog", (route) =>
    route.fulfill({ json: manifest.catalog }),
  );
  await page.route("**/api/v1/analytics/data-preview", (route) =>
    route.fulfill({ json: { period: result.period, warnings: [] } }),
  );
  await page.route("**/api/v1/analytics/bootstrap", (route) =>
    route.fulfill({ status: 202, json: bootstrapJob }),
  );
  await page.route(`**${bootstrapJob.status_url}`, (route) =>
    route.fulfill({ json: completed }),
  );
  await page.route(`**${bootstrapJob.result_url}`, (route) =>
    route.fulfill({ json: { ...completed, result } }),
  );
  await page.route(
    "**/api/v1/analytics/bootstrap-trend-sensitivity",
    (route) => {
      submissions++;
      expect(route.request().postDataJSON()).toEqual({
        bootstrap_job_id: bootstrapJob.job_id,
      });
      return route.fulfill({ status: 202, json: sensitivityJob });
    },
  );
  await page.route(`**${sensitivityJob.result_url}`, (route) =>
    route.fulfill({
      json: {
        ...sensitivityJob,
        status: "completed",
        result: { trend_sensitivity },
      },
    }),
  );
  await page.route(`**${sensitivityJob.status_url}`, (route) =>
    route.fulfill({
      json: sensitivityDone
        ? { ...sensitivityJob, status: "completed" }
        : sensitivityJob,
    }),
  );
  await page.goto("/");
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  await page.locator("input[type=file]").setInputFiles({
    name: "bootstrap-trend.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        schema: "qcross-portfolio-lab/v1",
        created_at: "test",
        instruments: manifest.catalog.instruments,
        calculations: [
          {
            id: "bootstrap-trend",
            kind: "bootstrap",
            origin: "demo",
            created_at: "test",
            request,
            result,
          },
        ],
      }),
    ),
  });
  await expect(page.getByText(/Снимок загружен локально/)).toBeVisible();
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page.getByRole("button", { name: "Bootstrap", exact: true }).click();

  const title = "Чувствительность к периоду SMA/Momentum";
  const plot = page.getByRole("img", { name: title });
  const calculateSensitivity = page.getByRole("button", {
    name: "Рассчитать чувствительность",
    exact: true,
  });
  await expect(calculateSensitivity).toHaveCount(0);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
  await expect(calculateSensitivity).toBeEnabled();
  await expect(plot).toHaveCount(0);
  expect(submissions).toBe(0);
  await calculateSensitivity.click();
  const progress = page
    .getByRole("status")
    .filter({ hasText: "Чувствительность SMA/Momentum · Bootstrap" });
  const cancel = progress.getByRole("button", { name: "Отменить ожидание" });
  await expect(progress).toContainText("running · 34.5 с · лимит 300 с");
  const originalViewport = page.viewportSize()!;
  for (const viewport of [originalViewport, { width: 390, height: 720 }]) {
    await page.setViewportSize(viewport);
    await page.evaluate(() =>
      window.scrollTo(0, document.documentElement.scrollHeight),
    );
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(200);
    await expect
      .poll(() => progress.evaluate((el) => el.getBoundingClientRect().top))
      .toBe(0);
    await expect(cancel).toBeInViewport({ ratio: 1 });
  }
  await page.setViewportSize(originalViewport);
  sensitivityDone = true;
  await expect(progress).toHaveCount(0);
  await expect(plot).toBeVisible();
  expect(submissions).toBe(1);
  const graph = plot.locator(".js-plotly-plot");
  const visiblePeriods = () =>
    graph.evaluate((el: any) =>
      el._fullData
        .filter((trace: any) => trace.visible === true)
        .map((trace: any) => trace.name),
    );
  await expect(plot.locator(".contourlayer .contour").first()).toBeVisible();
  expect(await visiblePeriods()).toEqual(["12 мес."]);
  expect(
    await graph.evaluate((el: any) => {
      const traces = el.data;
      return (
        traces.length === 36 &&
        traces.every(
          (trace: any) =>
            trace.type === "contour" &&
            trace.zmin === 0 &&
            trace.zmax === traces[0].zmax &&
            JSON.stringify([
              trace.x,
              trace.y,
              trace.contours.start,
              trace.contours.end,
              trace.contours.size,
            ]) ===
              JSON.stringify([
                traces[0].x,
                traces[0].y,
                traces[0].contours.start,
                traces[0].contours.end,
                traces[0].contours.size,
              ]),
        )
      );
    }),
  ).toBe(true);
  const firstLegend = plot.locator(".traces").filter({ hasText: /^1 мес\.$/ });
  await firstLegend.locator(".legendtoggle").click();
  await expect.poll(visiblePeriods).toEqual(["1 мес.", "12 мес."]);
  const section = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: title }) });
  await section.getByLabel("Риск").selectOption("2");
  await expect
    .poll(() => graph.evaluate((el: any) => el._fullLayout.xaxis.title.text))
    .toBe("Годовая волатильность");
  await expect.poll(visiblePeriods).toEqual(["1 мес.", "12 мес."]);
  await firstLegend.locator(".legendtoggle").click();
  await expect.poll(visiblePeriods).toEqual(["12 мес."]);
  const table = section.getByRole("table", {
    name: "Сравнение периодов SMA/Momentum",
  });
  await expect(table.locator("tbody tr")).toHaveCount(36);
  await expect(
    table.getByRole("columnheader", { name: "Среднегодовые комиссии" }),
  ).toBeVisible();
  await expect(
    section.getByText(
      /Годовые издержки Net: сумма списаний в долях капитала перед каждой сделкой/,
    ),
  ).toBeVisible();
  const annualTax = table.getByRole("columnheader", {
    name: "Годовой налог на прирост капитала",
  });
  await expect(annualTax).toBeVisible();
  await expect(
    table.getByRole("columnheader", { name: "Период" }),
  ).toHaveAttribute("aria-sort", "ascending");
  await expect(
    table.getByRole("columnheader", { name: "Sharpe" }),
  ).toBeVisible();
  await expect(
    table.getByRole("columnheader", { name: "Sortino" }),
  ).toBeVisible();
  await expect(
    table.getByRole("button", { name: "12 мес.", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    table.locator("tbody tr").first().getByRole("rowheader"),
  ).toHaveText("1 мес.");
  await table.getByRole("button", { name: "5 мес.", exact: true }).click();
  await expect(
    table.getByRole("button", { name: "5 мес.", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect.poll(visiblePeriods).toEqual(["5 мес.", "12 мес."]);
  await expect
    .poll(() => graph.evaluate((el: any) => el._fullData[4].contours.showlines))
    .toBe(true);
  await expect(
    section.getByRole("button", { name: "Net", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    table.locator("tbody tr").first().getByRole("cell").first(),
  ).toContainText("8.00%");
  await expect(
    table.locator("tbody tr").first().getByRole("cell").nth(3),
  ).toHaveCSS("background-color", "rgb(245, 188, 188)");
  await expect(
    table.locator("tbody tr").last().getByRole("cell").nth(3),
  ).toHaveCSS("background-color", "rgb(188, 245, 188)");
  await section.getByRole("button", { name: "Gross", exact: true }).click();
  await expect(annualTax).toHaveCount(0);
  await expect(
    table.locator("tbody tr").first().getByRole("cell").first(),
  ).toContainText("18.00%");
  await section.getByRole("button", { name: "Следующий график" }).click();
  await expect
    .poll(() => graph.evaluate((el: any) => el._fullLayout.yaxis.range[0]))
    .toBeGreaterThan(0.3);
  await expect.poll(visiblePeriods).toEqual(["5 мес.", "12 мес."]);
  await expect(
    table.locator("tbody tr").first().getByRole("cell").first(),
  ).toContainText("48.00%");
  expect(submissions).toBe(1);
});
