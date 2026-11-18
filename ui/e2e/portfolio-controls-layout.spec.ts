import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

for (const width of [1440, 390]) {
  test(`portfolio controls align with asset card at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    const assets = page.getByRole("region", {
      name: "Активы и пользовательский портфель",
    });
    const controls = page.getByRole("region", { name: "Управление портфелем" });
    await expect(controls).toBeVisible();
    const a = await assets.boundingBox(),
      b = await controls.boundingBox();
    expect(Math.abs(a!.x - b!.x)).toBeLessThan(1);
    expect(Math.abs(a!.width - b!.width)).toBeLessThan(1);
    expect(
      await controls.evaluate((el) => getComputedStyle(el).backgroundColor),
    ).toBe("rgb(255, 255, 255)");
  });
}

test("last asset picker opens above portfolio controls", async ({ page }) => {
  const manifest = JSON.parse(
    await readFile(resolve("public/demo/manifest.json"), "utf8"),
  );
  await page.route("**/api/v1/analytics/catalog", (route) =>
    route.fulfill({ json: manifest.catalog }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Live", exact: true }).click();
  const picker = page.getByRole("combobox", { name: /^Тикер \d+$/ }).last();
  await picker.scrollIntoViewIfNeeded();
  await picker.click();
  await page.evaluate(() => window.scrollBy(0, 300));
  const menu = page.getByRole("listbox").last();
  const controls = page.getByRole("region", { name: "Управление портфелем" });
  await expect(menu).toBeVisible();
  const bounds = await menu.boundingBox(),
    card = await controls.boundingBox();
  expect(bounds!.y + bounds!.height).toBeGreaterThan(card!.y);
  const x = bounds!.x + 20,
    y = card!.y + 10;
  expect(
    await menu.evaluate(
      (el, point) => el.contains(document.elementFromPoint(point.x, point.y)),
      { x, y },
    ),
  ).toBe(true);
});
