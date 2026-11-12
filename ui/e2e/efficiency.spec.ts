import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

test("Bootstrap histogram explains the interval midpoint and sample count on hover", async ({
  page,
}) => {
  const manifest = JSON.parse(
    await readFile(resolve("public/demo/manifest.json"), "utf8"),
  );
  const result = JSON.parse(
    await readFile(resolve("public/demo/bootstrap.json"), "utf8"),
  );
  const request = {
    ...result.reproducibility.api_request,
    user_weights: Object.fromEntries(
      result.assets.map((asset: string, i: number) => [asset, i === 0 ? 1 : 0]),
    ),
  };
  result.reproducibility.api_request = request;
  result.paired_comparison = {
    user_weights: request.user_weights,
    valid_count: 20,
    failed_count: 0,
    requested_comparisons: 20,
    generated_datasets: 40,
    horizon_months: 240,
    differences: {},
    win_frequencies: {},
    results: Array.from({ length: 20 }, (_, i) => ({
      status: "optimal",
      optimized: {},
      differences: {
        delta_cagr: -0.01 + i * 0.001,
        delta_volatility: -0.005 + i * 0.0005,
      },
      diagnostics: {},
    })),
  };
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/");
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  await page.locator("input[type=file]").setInputFiles({
    name: "bootstrap.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        schema: "qcross-portfolio-lab/v1",
        created_at: "test",
        instruments: manifest.catalog.instruments,
        calculations: [
          {
            id: "bootstrap-test",
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
  await page.getByRole("button", { name: "Bootstrap", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "Разница CAGR, п.п.",
    }),
  ).toBeVisible();
  const plot = page.getByRole("img", {
    name: /Bootstrap: Разница CAGR/,
  });
  const bar = plot.locator(".barlayer .point path").first();
  await expect(bar).toBeVisible();
  await bar.scrollIntoViewIfNeeded();
  const box = await bar.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  const label = plot.locator(".hoverlayer .hovertext");
  await expect(label).toContainText("Разница CAGR");
  await expect(label).toContainText("Середина интервала:");
  await expect(label).toContainText("п.п.");
  await expect(label).toContainText("Число пар:");
  await expect(label).toContainText(/Середина интервала:\s*[-−]?[\d.,]+/);
  await page
    .getByRole("button", {
      name: "Разница волатильности",
    })
    .click();
  const riskPlot = page.getByRole("img", {
    name: /Bootstrap: Разница волатильности/,
  });
  const riskBar = riskPlot.locator(".barlayer .point path").first();
  await expect(riskBar).toBeVisible();
  const riskBox = await riskBar.boundingBox();
  expect(riskBox).not.toBeNull();
  await page.mouse.move(
    riskBox!.x + riskBox!.width / 2,
    riskBox!.y + riskBox!.height / 2,
  );
  await expect(riskPlot.locator(".hoverlayer .hovertext")).toContainText(
    "Разница волатильности",
  );
  await expect(riskPlot.locator(".hoverlayer .hovertext")).toContainText(
    "Число пар:",
  );
});

for (const basis of ["nominal", "real"] as const) {
  test(`Live ${basis} fixed-portfolio Bootstrap distributions retain the full 240-month horizon`, async ({
    page,
  }) => {
    test.skip(
      !process.env.QCROSS_LIVE_TEST,
      "Requires local read-only stored-history API.",
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Live", exact: true }).click();
    await page.getByLabel("Аллокация 1", { exact: true }).fill("100");
    await page.getByLabel("Начало", { exact: true }).fill("2000-01-01");
    await page.getByLabel("Конец", { exact: true }).fill("2019-12-31");
    await page.getByLabel("Конец", { exact: true }).press("Escape");
    if (basis === "real")
      await page
        .getByRole("button", { name: "После инфляции", exact: true })
        .click();
    await page
      .getByRole("combobox", { name: "Метод ковариации", exact: true })
      .selectOption("sample");
    await page.getByLabel("Повторений", { exact: true }).fill("1000");
    await page.getByRole("button", { name: "Bootstrap", exact: true }).click();
    const finished = page.waitForResponse(
      async (r) =>
        /\/jobs\/[^/]+\/result$/.test(r.url()) &&
        (await r.json()).status === "completed",
    );
    await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
    const result = (await (await finished).json()).result;
    expect(result.return_basis).toBe(basis);
    expect(result.sample_length).toBe(240);
    const robustness = result.efficiency_robustness;
    expect(robustness.horizon.months).toBe(240);
    expect(robustness.results).toHaveLength(1000);
    expect(
      robustness.results.every(
        (r: { observations: number }) => r.observations === 240,
      ),
    ).toBe(true);
    expect(robustness.distributions.terminal_wealth.valid_count).toBe(1000);
    expect(robustness.distributions.historical_sharpe_ratio.horizon_years).toBe(
      20,
    );
    const table = page.getByRole("table", {
      name: "Распределения показателей фиксированного портфеля",
    });
    await expect(table.getByRole("row")).toHaveCount(9);
    await expect(
      table.getByText("240 мес. (20.00 лет)", { exact: true }),
    ).toHaveCount(8);
    await expect(
      page.getByText(/не являются калиброванными вероятностями/),
    ).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}
