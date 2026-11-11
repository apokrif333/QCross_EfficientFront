import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
test("paired Validation density clouds render and assets toggle independently", async ({
  page,
}) => {
  const manifest = JSON.parse(
    await readFile(resolve("public/demo/manifest.json"), "utf8"),
  );
  const result = JSON.parse(
    await readFile(resolve("public/demo/bootstrap.json"), "utf8"),
  );
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
  await page
    .getByRole("button", { name: "Cross-validation", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /скользящих 10-летних/ }),
  ).toHaveCount(0);
});
