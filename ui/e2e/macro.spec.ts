import { test, expect } from "@playwright/test";

test("Live Real BIL calculation uses reference metadata and both Sharpe metrics", async ({
  page,
}, testInfo) => {
  test.skip(
    !process.env.QCROSS_LIVE_TEST,
    "Requires imported local BIL/inflation references.",
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Начало", exact: true })
    .fill("1990-01-01");
  await page
    .getByRole("textbox", { name: "Начало", exact: true })
    .press("Escape");
  await page
    .getByRole("textbox", { name: "Конец", exact: true })
    .fill("2026-08-31");
  await page
    .getByRole("textbox", { name: "Конец", exact: true })
    .press("Escape");
  await page
    .getByRole("button", { name: "После инфляции", exact: true })
    .click();
  const finished = page.waitForResponse(async (response) => {
    if (!/\/jobs\/[^/]+\/result$/.test(response.url())) return false;
    return (await response.json()).status === "completed";
  });
  await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
  const result = (await (await finished).json()).result;
  expect(result.return_basis).toBe("real");
  expect(result.risk_free_source).toBe("historical_bil");
  expect(result.reference_metadata.risk_free_instrument).toBe("BIL");
  expect(result.max_sharpe.metrics.historical_sharpe_ratio).toEqual(
    expect.any(Number),
  );
  expect(result.max_sharpe.diagnostics.sharpe_objective).toBe(
    "estimated_differential_returns",
  );
  await expect(page.getByText(/Real USD · Historical BIL/)).toBeVisible();
  await expect(
    page.getByText(/Показан ранее полученный результат/),
  ).toHaveCount(0);
  await expect(
    page.getByRole("columnheader", { name: "Historical Sharpe", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("macro-real-bil-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".settings-shell>summary").click();
  await expect
    .poll(() =>
      page
        .locator(".plot-wrap .main-svg")
        .first()
        .evaluate((el) => el.getBoundingClientRect().width),
    )
    .toBeLessThan(365);
  await page.screenshot({
    path: testInfo.outputPath("macro-real-bil-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("legacy Demo basis is explicit and new Live requests default to BIL", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Efficient Frontier", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Безрисковая доходность", exact: true }),
  ).toHaveValue("fixed_annual");
  await page.route("**/api/v1/analytics/catalog", async (route) => {
    const manifest = await page.request.get("/demo/manifest.json");
    await route.fulfill({ json: (await manifest.json()).catalog });
  });
  await page.route("**/api/v1/analytics/data-preview", (route) =>
    route.fulfill({
      json: {
        period: { start: "1990-01", end: "2026-08", observations: 440 },
        warnings: [],
      },
    }),
  );
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Безрисковая доходность", exact: true }),
  ).toHaveValue("historical_bil");
  await expect(
    page.getByRole("spinbutton", {
      name: "Безрисковая ставка, %",
      exact: true,
    }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "После инфляции", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "После инфляции", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({
    path: testInfo.outputPath("macro-settings-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: testInfo.outputPath("macro-settings-mobile.png"),
    fullPage: true,
  });
});
