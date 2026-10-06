import { test, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

test("Demo works without API; charts, mobile, snapshot export and reload", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Efficient Frontier", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  await page.getByRole("button", { name: "GMV", exact: true }).click();
  await expect(page.locator(".point-detail h3")).toHaveText("GMV");
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(
    page.locator(".legendtext").filter({ hasText: /^Nonlinear$/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Cross-validation", exact: true })
    .click();
  await expect(
    page.getByText("Устойчивость весов", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Bootstrap", exact: true }).click();
  await expect(page.getByText(/1000 \/ 1000 успешных/)).toBeVisible();
  await expect(page.locator(".js-plotly-plot")).toHaveCount(3);
  await expect(
    page
      .getByRole("img", { name: "Распределение bootstrap весов" })
      .locator(".barlayer .point")
      .first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Frontier", exact: true }).click();
  await mkdir(resolve("../docs/ui-verification"), { recursive: true });
  await page.screenshot({
    path: resolve("../docs/ui-verification/demo-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".settings-shell>summary").click();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await expect
    .poll(() =>
      page
        .locator(".plot-wrap .main-svg")
        .first()
        .evaluate((el) => el.getBoundingClientRect().width),
    )
    .toBeLessThan(365);
  await page.screenshot({
    path: resolve("../docs/ui-verification/demo-mobile.png"),
    fullPage: true,
  });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Сохранить JSON" }).click();
  const path = await (await download).path();
  await page.locator("input[type=file]").setInputFiles(path!);
  await expect(
    page.getByText("Сохранённые расчёты (7)", { exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("all four Live jobs, four-method comparison and exact snapshot repeat through UI", async ({
  page,
}) => {
  test.skip(
    !process.env.QCROSS_LIVE_TEST,
    "Start real FastAPI and set QCROSS_LIVE_TEST=1 for stored-history integration.",
  );
  const jobs: {
    kind: string;
    seconds: number;
    status: string;
    hash: string;
  }[] = [];
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", async (response) => {
    if (
      /\/api\/v1\/analytics\/jobs\/[^/]+\/result$/.test(response.url()) &&
      response.ok()
    ) {
      const body = await response.json();
      if (body.result)
        jobs.push({
          kind: body.kind,
          seconds: body.elapsed_seconds,
          status: body.result.status,
          hash: body.result.reproducibility.returns_matrix_sha256,
        });
    }
  });
  await page.goto("/");
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page.getByLabel("Начало", { exact: true }).fill("1990-01-01");
  await expect(
    page.getByText("LIVE · FastAPI", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Проверить период и пропуски" })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Период" }),
  ).toContainText("440 месяцев");
  const run = async () => {
    await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Рассчитать", exact: true }),
    ).toBeEnabled();
    await expect(page.getByRole("alert")).toHaveCount(0);
  };
  await run();
  await page
    .getByRole("button", { name: "Cross-validation", exact: true })
    .click();
  await run();
  await expect(
    page.getByText("Устойчивость весов", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Bootstrap", exact: true }).click();
  await run();
  await expect(page.getByText(/1000 \/ 1000 успешных/)).toBeVisible();
  await page
    .locator("main .segmented")
    .getByRole("button", { name: "Max Sharpe", exact: true })
    .click();
  await expect(page.getByText(/1000 \/ 1000 успешных/)).toBeVisible();
  await page.getByLabel("Повторений", { exact: true }).fill("250");
  await page
    .getByRole("button", { name: "Рассчитать Resampled Frontier", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Рассчитать Resampled Frontier",
      exact: true,
    }),
  ).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText(/250 \/ 250 успешных/)).toBeVisible();
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await page
    .getByRole("button", { name: "Сравнить 4 метода", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Сравнить 4 метода", exact: true }),
  ).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.locator(".legendtext").filter({ hasText: /^CV Linear$/ }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve("../docs/ui-verification/live-compare.png"),
    fullPage: true,
  });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Сохранить JSON" }).click();
  await page
    .locator("input[type=file]")
    .setInputFiles((await (await download).path())!);
  await expect(
    page.getByText("Сохранённые расчёты (7)", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Повторить снимок через Live", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Повторить снимок через Live",
      exact: true,
    }),
  ).toBeEnabled({ timeout: 90000 });
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText(/Численные результаты совпадают/)).toHaveCount(7);
  expect(jobs.filter((j) => j.status === "complete")).toHaveLength(15);
  expect(new Set(jobs.map((j) => j.hash)).size).toBe(1);
  expect(errors).toEqual([]);
  await writeFile(
    resolve("../docs/ui-verification/live-jobs.json"),
    JSON.stringify(jobs, null, 2),
  );
});

test("API validation error stays visible and is never replaced with a Demo result", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  await page.route("**/api/v1/analytics/catalog", (route) =>
    route.fulfill({ json: { engine: { version: "test" }, instruments: [] } }),
  );
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page.route("**/api/v1/analytics/frontier", (route) =>
    route.fulfill({
      status: 422,
      json: { detail: "Allocation constraints are infeasible." },
    }),
  );
  await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Allocation constraints are infeasible.",
  );
  await expect(page.locator(".js-plotly-plot")).toHaveCount(0);
});

test("loaded snapshot displays numerical differences and provenance warnings on replay (mock API)", async ({
  page,
}) => {
  const result = JSON.parse(
    await readFile(
      resolve("../docs/analytics-demo/frontier-sample.json"),
      "utf8",
    ),
  );
  const changed = structuredClone(result);
  changed.gmv.weights["1"] += 0.01;
  changed.reproducibility.returns_matrix_sha256 = "0".repeat(64);
  const instruments = result.source_metadata.map(
    (i: Record<string, unknown>) => ({
      ...i,
      id: i.instrument_id,
      name: i.ticker,
      category: null,
    }),
  );
  const snapshot = {
    schema: "qcross-portfolio-lab/v1",
    created_at: "test",
    instruments,
    calculations: [
      {
        id: "comparison-test",
        kind: "frontier",
        request: result.reproducibility.api_request,
        result: changed,
        origin: "demo",
        created_at: "test",
      },
    ],
  };
  await page.route("**/api/v1/analytics/catalog", (route) =>
    route.fulfill({
      json: { engine: result.reproducibility.engine, instruments },
    }),
  );
  await page.route("**/api/v1/analytics/frontier", (route) =>
    route.fulfill({
      status: 202,
      json: {
        job_id: "mock",
        kind: "frontier",
        status: "completed",
        timeout_seconds: 300,
        elapsed_seconds: 0,
        result_url: "/api/v1/analytics/jobs/mock/result",
      },
    }),
  );
  await page.route("**/api/v1/analytics/jobs/mock/result", (route) =>
    route.fulfill({ json: { status: "completed", result } }),
  );
  await page.goto("/");
  await expect(page.locator(".js-plotly-plot")).toHaveCount(1);
  await page.locator("input[type=file]").setInputFiles({
    name: "changed.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(snapshot)),
  });
  await page
    .getByRole("button", { name: "Повторить снимок через Live", exact: true })
    .click();
  await expect(page.getByText(/различий за пределами допусков/)).toBeVisible();
  await expect(
    page.getByText("SHA-256 использованной матрицы доходностей изменился.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "result.gmv.weights.1", exact: true }),
  ).toBeVisible();
});
