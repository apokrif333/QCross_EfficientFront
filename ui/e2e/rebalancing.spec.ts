import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { rebalancingFixture } from "../src/test/rebalancingFixture";
test("rebalancing snapshot renders 3D, twenty strategies, weights and events", async ({
  page,
}) => {
  const result = rebalancingFixture();
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
      .getByRole("table", { name: "Сравнение 20 правил ребалансировки" })
      .locator("tbody tr"),
  ).toHaveCount(20);
  const chart = page.getByRole("img", {
    name: "3D Rebalancing Robustness",
    exact: true,
  });
  await expect(chart.locator("canvas").first()).toBeVisible();
  const data = await chart.locator(".js-plotly-plot").evaluate((el: any) => ({
    type: el._fullData[0].type,
    z: el._fullData[0].z,
  }));
  expect(data.type).toBe("scatter3d");
  expect(data.z).toHaveLength(13);
  await page
    .getByRole("combobox", { name: "Стратегия для весов и сделок" })
    .selectOption("threshold_10");
  await page.getByText("Сделки и события: Порог 10%", { exact: true }).click();
  await expect(
    page.getByRole("columnheader", { name: "Триггеры" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "До комиссий", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Кривые капитала до комиссий" }),
  ).toBeVisible();
});
