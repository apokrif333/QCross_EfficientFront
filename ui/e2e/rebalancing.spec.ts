import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { rebalancingFixture } from "../src/test/rebalancingFixture";
for (const trend of [false, true]) {
  test(`rebalancing snapshot renders strategies and controls: ${trend}`, async ({
    page,
  }) => {
    const result = rebalancingFixture(
      trend
        ? {
            mode: "trend",
            hedge_weights: { "243": 1 },
            trend: {
              destination: "hedge",
              asset_rules: { "1": { indicator: "sma", lookback_months: 12 } },
              sensitivity_asset: "1",
            },
          }
        : undefined,
    );
    const manifest = JSON.parse(
      await readFile(resolve("public/demo/manifest.json"), "utf8"),
    );
    await page.route("**/api/**", (r) => r.abort());
    await page.goto("/");
    await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
    await page.locator("input[type=file]").setInputFiles({
      name: "rebalancing.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({
          schema: "qcross-portfolio-lab/v1",
          created_at: "test",
          instruments: manifest.catalog.instruments,
          calculations: [
            {
              id: "test",
              kind: "rebalancing-optimization",
              request: result.reproducibility.api_request,
              result,
              origin: "demo",
              created_at: "test",
            },
          ],
        }),
      ),
    });
    await expect(page.getByText(/Снимок загружен локально/)).toBeVisible();
    await page
      .getByRole("button", { name: "Rebalancing Optimization", exact: true })
      .click();
    await expect(
      page
        .getByRole("table", { name: "Сравнение правил ребалансировки" })
        .locator("tbody tr"),
    ).toHaveCount(21);
    const table = page.getByRole("table", {
      name: "Сравнение правил ребалансировки",
    });
    const firstCell = table.locator("tbody tr").first().locator("td").first();
    const before = await firstCell.boundingBox();
    await table.locator("..").evaluate((el) => {
      el.scrollLeft = el.scrollWidth;
    });
    const after = await firstCell.boundingBox();
    expect(Math.abs(after!.x - before!.x)).toBeLessThan(2);
    const chart = page.getByRole("img", {
      name: "Правила ребалансировки: фактические доходность и риск",
      exact: true,
    });
    await expect(chart.locator(".js-plotly-plot")).toBeVisible();
    const data = await chart.locator(".js-plotly-plot").evaluate((el: any) => ({
      type: el._fullData[0].type,
      count: el._fullData.filter((trace: any) => trace.showlegend).length,
    }));
    expect(data.type).toBe("scatter");
    expect(data.count).toBe(21);
    if (trend) {
      await expect(
        page.getByRole("heading", {
          name: "Управление портфелем: исходный / управляемый",
        }),
      ).toBeVisible();
      const sensitivity = page.getByRole("img", {
        name: /^Чувствительность SMA\/Momentum:/,
      });
      await sensitivity.scrollIntoViewIfNeeded();
      await expect(sensitivity.locator("canvas").first()).toBeVisible();
      const z = await sensitivity
        .locator(".js-plotly-plot")
        .evaluate((el: any) => el._fullData[0].z);
      expect(z).toHaveLength(35);
    }
    await page
      .getByRole("combobox", { name: "Стратегия для весов и сделок" })
      .selectOption("threshold_10");
    await page
      .getByText("Сделки и события: Порог 10%", { exact: true })
      .click();
    await expect(
      page.getByRole("columnheader", { name: "Триггеры" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "До комиссий", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Кривые капитала до комиссий" }),
    ).toBeVisible();
  });
}
