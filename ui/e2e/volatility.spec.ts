import { test, expect } from "@playwright/test";

test("Frontier and Compare use model volatility in Plotly, hover, table and details", async ({
  page,
}) => {
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/");
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  for (const compare of [false, true]) {
    if (compare)
      await page.getByRole("button", { name: "Compare", exact: true }).click();
    const traces = await page.locator(".js-plotly-plot").evaluate((el) => {
      const plot = el as unknown as {
        data: {
          name: string;
          x: number[];
          hovertemplate: string[];
          customdata: {
            metrics: {
              volatility: number;
              historical_volatility: number;
              sharpe_ratio: number;
            };
          }[];
        }[];
      };
      return plot.data;
    });
    expect(traces.length).toBeGreaterThan(3);
    for (const trace of traces) {
      for (const [i, p] of trace.customdata.entries()) {
        expect(trace.x[i]).toBe(p.metrics.volatility);
        expect(trace.hovertemplate[i]).toContain(
          `Волатильность ${(p.metrics.volatility * 100).toFixed(2)}%`,
        );
        if (trace.name === "Активы") {
          expect(trace.hovertemplate[i]).not.toContain("Estimated Sharpe");
        } else if (p.metrics.sharpe_ratio !== null) {
          expect(trace.hovertemplate[i]).toContain(
            `Sharpe ${p.metrics.sharpe_ratio.toFixed(3)}`,
          );
        }
      }
    }
    const gmv = traces.find((t) =>
      compare ? t.name.endsWith("· GMV") : t.name === "GMV",
    )!;
    const formatted = `${(gmv.customdata[0].metrics.volatility * 100).toFixed(2)}%`;
    const button = page.getByRole("button", { name: gmv.name, exact: true });
    await expect(
      button.locator("xpath=ancestor::tr").locator("td").nth(2),
    ).toHaveText(formatted);
    await button.click();
    await expect(
      page
        .locator(".point-detail .metrics span")
        .filter({ hasText: "Волатильность" }),
    ).toHaveText(`Волатильность${formatted}`);
  }
});
