import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("one-sided CV directions, periods and controls render on desktop and mobile", async ({
  page,
}) => {
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/");
  await expect(
    page.getByRole("combobox", { name: "Метод ковариации", exact: true }),
  ).toHaveValue("nonlinear");
  await page
    .getByRole("combobox", { name: "Метод в Demo", exact: true })
    .selectOption("sample");
  await page
    .getByRole("button", { name: "Cross-validation", exact: true })
    .click();
  await expect(
    page.getByText(/Двунаправленная односторонняя блочная CV/),
  ).toBeVisible();
  await expect(page.getByText("После проверки", { exact: true })).toHaveCount(
    2,
  );
  await expect(page.getByText("До проверки", { exact: true })).toHaveCount(2);
  await expect(page.getByLabel("Число CV-фолдов")).toHaveValue("4");
  await expect(
    page.getByRole("heading", {
      name: "Bidirectional One-Sided Block Cross-Validation",
    }),
  ).toBeVisible();
  await expect(page.locator(".barlayer .point").first()).toBeVisible();
  await expect(
    page.getByLabel("Число CV-фолдов").locator("option"),
  ).toHaveCount(10);
  await page.screenshot({
    path: resolve("../docs/ui-verification/one-sided-cv-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".settings-shell>summary").click();
  await expect(page.locator(".barlayer .point").first()).toBeVisible();
  await expect
    .poll(() =>
      page
        .locator(".plot-wrap .main-svg")
        .first()
        .evaluate((el) => el.getBoundingClientRect().width),
    )
    .toBeLessThan(365);
  await page.screenshot({
    path: resolve("../docs/ui-verification/one-sided-cv-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
