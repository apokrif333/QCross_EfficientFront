import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

test.setTimeout(45000);
test.use({ actionTimeout: 10000 });

test("rebalancing is available only without portfolio controls", async ({
  page,
}) => {
  const manifest = JSON.parse(
    await readFile(resolve("public/demo/manifest.json"), "utf8"),
  );
  await page.route("**/api/v1/analytics/catalog", (route) =>
    route.fulfill({ json: manifest.catalog }),
  );
  await page.route("**/api/v1/analytics/data-preview", (route) =>
    route.fulfill({
      json: {
        period: { start: "2000-01", end: "2025-12", observations: 312 },
        warnings: [],
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page
    .getByRole("button", { name: "Rebalancing Optimization", exact: true })
    .click();
  const calculate = page.getByRole("button", {
    name: "Рассчитать",
    exact: true,
  });
  await expect(
    page.getByText(
      "Оптимизация ребалансировки доступна только для пассивного портфеля.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(calculate).toBeEnabled();
  const mode = page
    .getByRole("region", { name: "Управление портфелем" })
    .getByRole("combobox", { name: "Режим", exact: true });
  for (const value of ["downside_vol", "trend"]) {
    await mode.selectOption(value);
    await expect(calculate).toBeDisabled();
    await expect(
      page.getByText(/Для расчёта выберите режим «Без управления»/),
    ).toBeVisible();
    await expect(
      page.getByRole("table", { name: "Сравнение правил ребалансировки" }),
    ).toHaveCount(0);
  }
  await mode.selectOption("none");
  await expect(calculate).toBeEnabled();
});
