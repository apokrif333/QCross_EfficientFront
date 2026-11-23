import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
test("Bootstrap switches from Validation to a matched passive comparison for My Portfolio", async ({
  page,
}) => {
  const manifest = JSON.parse(
    await readFile(resolve("public/demo/manifest.json"), "utf8"),
  );
  const result = JSON.parse(
    await readFile(resolve("public/demo/bootstrap.json"), "utf8"),
  );
  result.objectives.max_return_at_user_risk = result.objectives.gmv;
  result.portfolio_control_samples = Array.from({ length: 500 }, (_, i) => {
    const active = {
      annualized_cagr: 0.08,
      annualized_arithmetic_return: 0.09 + 0.03 * Math.cos(i * 0.96),
      annualized_volatility: 0.1 + 0.02 * Math.sin(i * 1.36),
      maximum_drawdown: 0.2,
    };
    return {
      controlled: { net: active },
      baseline: {
        net: {
          ...active,
          annualized_cagr: 0.1,
          annualized_arithmetic_return:
            active.annualized_arithmetic_return + 0.02,
          annualized_volatility: active.annualized_volatility + 0.01,
        },
      },
    };
  });
  result.paired_comparison = {
    valid_count: 250,
    failed_count: 0,
    requested_comparisons: 250,
    generated_datasets: 500,
    horizon_months: 240,
    differences: {},
    win_frequencies: {},
    results: [],
    clouds: Array.from({ length: 250 }, (_, i) => {
      const x = 0.1 + 0.02 * Math.sin(i * 1.36),
        y = 0.08 + 0.03 * Math.cos(i * 0.96);
      return {
        pair: i + 1,
        assets: Object.fromEntries(
          result.assets.map((a: string) => [
            a,
            { expected_return: y * 1.4, volatility: x * 1.5 },
          ]),
        ),
        user: { expected_return: y, volatility: x },
        optimized: { expected_return: y + 0.01, volatility: x + 0.003 },
      };
    }),
    validation_frontier: [
      { expected_return: 0.02, volatility: 0.04 },
      { expected_return: 0.08, volatility: 0.1 },
    ],
  };
  await page.route("**/api/**", (r) => r.abort());
  await page.goto("/");
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  await page.locator("input[type=file]").setInputFiles({
    name: "paired.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        schema: "qcross-portfolio-lab/v1",
        created_at: "test",
        instruments: manifest.catalog.instruments,
        calculations: [
          {
            id: "paired",
            kind: "bootstrap",
            origin: "demo",
            created_at: "test",
            request: result.reproducibility.api_request,
            result,
          },
        ],
      }),
    ),
  });
  await expect(page.getByText(/Снимок загружен локально/)).toBeVisible();
  await page.getByRole("button", { name: "Bootstrap", exact: true }).click();
  const plot = page.getByRole("img", { name: "Validation density clouds" });
  await expect(plot).toBeVisible();
  await expect(plot.locator(".contourlayer .contour").first()).toBeVisible();
  const graph = plot.locator(".js-plotly-plot");
  const info = await graph.evaluate((el: any) =>
    el._fullData.map((t: any) => ({
      name: t.name,
      type: t.type,
      count: t.x?.length,
      visible: t.visible,
    })),
  );
  expect(
    info.filter(
      (t: any) => t.type === "histogram2dcontour" && t.visible === true,
    ),
  ).toHaveLength(2);
  expect(info[0].count).toBe(250);
  expect(info[0].visible).toBe("legendonly");
  await plot.locator(".legendtoggle").first().click();
  await expect
    .poll(() => graph.evaluate((el: any) => el._fullData[0].visible))
    .toBe(true);
  const switcher = page.getByRole("group", {
    name: "С чем сравнивать мой портфель",
  });
  await expect(switcher).toHaveCount(0);
  await page.getByRole("button", { name: "Мой портфель", exact: true }).click();
  await expect(
    switcher.getByRole("button", { name: "Validation" }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("heading", {
      name: "Ваш портфель и оптимизатор — проверка на Validation",
    }),
  ).toBeVisible();
  await switcher.getByRole("button", { name: "Пассивный портфель" }).click();
  await expect(
    page.getByRole("heading", {
      name: "Ваш активный и пассивный портфели — Stationary Bootstrap",
    }),
  ).toBeVisible();
  await expect(page.getByText(/500 генераций · 500 пар/)).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Распределение разниц между активным и пассивным портфелями",
    }),
  ).toBeVisible();
  const passivePlot = page.getByRole("img", {
    name: "Плотность доходности и риска активного и пассивного портфелей",
  });
  await expect(passivePlot.locator(".contourlayer .contour")).toHaveCount(2);
  await expect(
    passivePlot.getByText("Пассивный портфель (500)", { exact: true }),
  ).toBeVisible();
  const histogram = page
    .getByRole("img", { name: "Bootstrap: Разница CAGR" })
    .locator(".js-plotly-plot");
  await expect
    .poll(() =>
      histogram.evaluate((el: any) => ({
        count: el.data[0].x.length,
        sign: Math.sign(el.data[0].x[0]),
      })),
    )
    .toEqual({ count: 500, sign: 1 });

  await switcher.getByRole("button", { name: "Validation" }).click();
  await expect(plot).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Распределение разниц на Validation" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Cross-validation", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /скользящих 10-летних/ }),
  ).toHaveCount(0);
});
