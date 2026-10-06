import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { mkdir } from "node:fs/promises";

test("Live asset builder, numbered constraints and a 100% VTI reference use actual stored history", async ({
  page,
}) => {
  test.skip(
    !process.env.QCROSS_LIVE_TEST,
    "Requires the local stored-history API.",
  );
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await expect(page.getByLabel("Тикер 1")).toHaveValue("VTI");
  expect(
    await page
      .locator(".asset-builder .asset-grid:not(.asset-grid-heading)")
      .count(),
  ).toBe(5);
  await page.getByLabel("Тикер 4").click();
  await expect(page.getByRole("listbox")).toBeVisible();
  await expect(
    page.getByRole("group", { name: "US Stocks", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Тикер 4").fill("IJS");
  await page.getByRole("option", { name: /IJS/ }).click();
  await expect(page.getByLabel("Тикер 4")).toHaveValue("IJS");
  await page.getByRole("button", { name: "Очистить актив 4" }).click();
  await page.getByLabel("Группа актива 2").selectOption("3");
  await page.getByLabel("Группа актива 3").selectOption("5");
  await expect(page.getByLabel("Группа 3 максимум %")).toBeVisible();
  await expect(page.getByLabel("Группа 5 максимум %")).toBeVisible();
  await expect(page.getByLabel("Группа 2 максимум %")).toHaveCount(0);
  await page.getByLabel("Группа 1 максимум %").fill("80");
  await page.getByLabel("Аллокация 1", { exact: true }).fill("99");
  await expect(page.getByRole("alert")).toContainText("100% или 0%");
  await expect(
    page.getByRole("button", { name: "Рассчитать", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Аллокация 1", { exact: true }).fill("100");
  await page.getByRole("button", { name: "Добавить актив" }).click();
  expect(
    await page
      .locator(".asset-builder .asset-grid:not(.asset-grid-heading)")
      .count(),
  ).toBe(6);
  const resultPromise = page.waitForResponse(async (response) => {
    if (!/\/jobs\/[^/]+\/result$/.test(response.url())) return false;
    const body = await response.json();
    return body.status === "completed";
  });
  await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
  const result = (await (await resultPromise).json()).result;
  expect(result.period).toEqual({
    start: "1793-01",
    end: "2026-08",
    observations: 2804,
  });
  expect(result.user_portfolio.metrics.expected_return).toBeCloseTo(
    0.09228035948644794,
    12,
  );
  expect(result.user_portfolio.metrics.historical_cagr).toBeCloseTo(
    0.08450306539472936,
    12,
  );
  expect(result.user_portfolio.metrics.historical_volatility).toBeCloseTo(
    0.1478254835708333,
    12,
  );
  expect(result.user_portfolio.feasible).toBe(false);
  expect(result.gmv.weights["1"]).toBeLessThanOrEqual(0.8 + 1e-7);
  expect(result.reproducibility.api_request.asset_groups).toEqual({
    "1": 1,
    "175": 3,
    "243": 5,
  });
  await page.getByRole("button", { name: "Ваш портфель", exact: true }).click();
  await expect(
    page.getByRole("columnheader", { name: "Ожидаемая (арифм.)" }),
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: "Волатильность", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".point-detail")).toContainText("8.45%");
  await expect(page.locator(".point-detail")).toContainText("9.23%");
  await expect(page.locator(".point-detail")).toContainText(
    "нарушает заданные ограничения",
  );
  await expect(page.getByLabel("Отображение риска и доходности")).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("columnheader", { name: "Историческая волатильность" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("columnheader", { name: "Оценка волатильности" }),
  ).toHaveCount(0);
  const plotted = await page.locator(".js-plotly-plot").evaluate((el) => {
    const plot = el as unknown as {
      data: { name: string; x: number[]; y: number[] }[];
      layout: { yaxis: { title: { text: string } } };
    };
    return {
      user: plot.data.find((trace) => trace.name === "Ваш портфель"),
      axis: plot.layout.yaxis.title.text,
    };
  });
  expect(plotted.axis).toBe("Историческая CAGR");
  expect(plotted.user!.y[0]).toBeCloseTo(
    result.user_portfolio.metrics.historical_cagr,
    12,
  );
  expect(plotted.user!.x[0]).toBeCloseTo(
    result.user_portfolio.metrics.historical_volatility,
    12,
  );
  const assetPoints = await page.locator(".js-plotly-plot").evaluate((el) => {
    const plot = el as unknown as {
      data: {
        marker?: { symbol?: string; color?: string };
        text?: string[];
        x: number[];
        y: number[];
      }[];
    };
    return plot.data.find((trace) => trace.marker?.symbol === "diamond");
  });
  expect(assetPoints!.text).toEqual(["VTI", "TLT", "GLD"]);
  expect(assetPoints!.marker!.color).toBe("#d14b74");
  for (const [index, asset] of ["1", "175", "243"].entries()) {
    expect(assetPoints!.y[index]).toBeCloseTo(
      result.asset_portfolios[asset].metrics.historical_cagr,
      12,
    );
    expect(assetPoints!.x[index]).toBeCloseTo(
      result.asset_portfolios[asset].metrics.historical_volatility,
      12,
    );
  }
  await mkdir(resolve("../docs/ui-verification"), { recursive: true });
  await page.screenshot({
    path: resolve("../docs/ui-verification/asset-builder-live-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: resolve("../docs/ui-verification/asset-builder-live-mobile.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
