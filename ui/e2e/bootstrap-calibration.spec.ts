import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("Bootstrap Calibration works offline and applies L only on request", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/");
  await page
    .getByRole("button", { name: "Bootstrap Calibration", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Bootstrap Block Length Calibration" }),
  ).toBeVisible();
  await expect(page.getByText(/полных Calibration Fold 4/)).toBeVisible();
  const crps = page.getByRole("img", { name: "Aggregated Normalized CRPS" });
  await expect(crps.locator(".scatterlayer .trace").first()).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Скачать результат JSON" }).click();
  const exported = JSON.parse(
    await readFile((await (await download).path())!, "utf8"),
  );
  expect(exported.methodology).toBe(
    "chronological_bootstrap_block_length_calibration_v1",
  );
  expect(exported.reproducibility.sample_fingerprints["1"]["3"]).toHaveLength(
    64,
  );
  await expect(
    page
      .getByRole("img", { name: "Тепловая карта Normalized CRPS" })
      .locator(".heatmaplayer image")
      .first(),
  ).toBeVisible();
  await page.getByLabel("Критерий графика").selectOption("energy");
  await expect(
    page
      .getByRole("img", { name: "Energy Score" })
      .locator(".scatterlayer .trace")
      .first(),
  ).toBeVisible();
  await page
    .getByText("Распределения Bootstrap и реальный Test", { exact: true })
    .click();
  await expect(page.getByLabel("Реальный интервал")).toBeVisible();
  await page
    .getByRole("combobox", { name: "Характеристика", exact: true })
    .selectOption("acf_returns");
  await expect(
    page
      .getByRole("img", { name: "ACF Returns", exact: true })
      .locator(".scatterlayer .trace")
      .first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Показать сохранённый Final Holdout" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Final Holdout", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Выбранная длина блока").first().selectOption("6");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.getByRole("button", { name: "Use Selected Block Length" }).click();
  await expect(
    page.getByRole("button", { name: "Bootstrap", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.getByLabel(/Длина блока, мес/)).toHaveValue("6");
  expect(errors).toEqual([]);
});

test("Bootstrap Calibration Live verifies real periods, stores detail, and freezes Holdout", async ({
  page,
}) => {
  test.skip(
    !process.env.QCROSS_LIVE_TEST,
    "Requires the local QCross database and FastAPI.",
  );
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST")
      requests.push(new URL(request.url()).pathname);
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page
    .getByRole("button", { name: "Bootstrap Calibration", exact: true })
    .click();
  await page.getByLabel("Начальная историческая дата").fill("1980-01-01");
  await page.getByLabel("Конечная историческая дата").fill("1992-08-31");
  await page
    .getByRole("spinbutton", { name: "Train, мес.", exact: true })
    .fill("48");
  await page
    .getByRole("spinbutton", { name: "Test, мес.", exact: true })
    .fill("48");
  await page
    .getByRole("spinbutton", { name: "Bootstrap-генераций", exact: true })
    .fill("48");
  await page
    .getByRole("spinbutton", { name: "Максимальная длина блока", exact: true })
    .fill("12");
  await page
    .getByRole("spinbutton", { name: "Шаг длины блока", exact: true })
    .fill("9");
  for (const horizon of [60, 120, 240])
    await page
      .getByRole("checkbox", { name: horizon + " мес.", exact: true })
      .uncheck();
  await page.getByRole("button", { name: "Проверить периоды" }).click();
  await expect(page.getByText(/полных Calibration Fold 2/)).toBeVisible();
  await page.getByRole("button", { name: "Запустить Calibration" }).click();
  await expect(
    page.getByRole("img", { name: "Aggregated Normalized CRPS" }),
  ).toBeVisible();
  await page
    .getByText("Распределения Bootstrap и реальный Test", { exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Реальный интервал" }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Характеристика" })
    .selectOption("acf_returns");
  await expect(page.getByRole("img", { name: "ACF Returns" })).toBeVisible();
  await page.getByRole("button", { name: "Запустить Final Holdout" }).click();
  await expect(
    page.getByRole("heading", { name: "Final Holdout", exact: true }),
  ).toBeVisible();
  expect(
    requests.filter((path) => path.endsWith("/bootstrap-calibration")),
  ).toHaveLength(1);
  expect(
    requests.filter((path) => path.endsWith("/bootstrap-calibration-holdout")),
  ).toHaveLength(1);
  expect(requests.filter((path) => path.endsWith("/bootstrap"))).toHaveLength(
    0,
  );
});
