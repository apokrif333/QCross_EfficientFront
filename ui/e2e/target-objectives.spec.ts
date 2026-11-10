import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

test("App preserves an explicit Target Return Bootstrap list from a loaded request", async ({
  page,
}) => {
  const manifest = JSON.parse(
    await readFile(resolve("public/demo/manifest.json"), "utf8"),
  );
  const result = JSON.parse(
    await readFile(resolve("public/demo/bootstrap.json"), "utf8"),
  );
  const request = {
    ...result.reproducibility.api_request,
    objective: "gmv",
    target_return: 0.08,
    bootstrap_objectives: ["target_return", "gmv"],
    bootstrap_iterations: 3,
  };
  result.reproducibility.api_request = request;
  await page.route("**/api/v1/analytics/catalog", (route) =>
    route.fulfill({ json: manifest.catalog }),
  );
  await page.route("**/api/v1/analytics/data-preview", (route) =>
    route.fulfill({ json: { period: result.period } }),
  );
  await page.route("**/api/v1/analytics/bootstrap", (route) =>
    route.fulfill({
      status: 422,
      json: { detail: "Payload captured for regression test" },
    }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("combobox", { name: "Метод в Demo", exact: true }),
  ).toBeEnabled();
  await page.locator("input[type=file]").setInputFiles({
    name: "target-request.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        schema: "qcross-portfolio-lab/v1",
        created_at: "test",
        instruments: manifest.catalog.instruments,
        calculations: [
          {
            id: "target-bootstrap",
            kind: "bootstrap",
            origin: "live",
            created_at: "test",
            request,
            result,
          },
        ],
      }),
    ),
  });
  await expect(page.getByText(/Снимок загружен локально/)).toBeVisible();
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page.getByRole("button", { name: "Bootstrap", exact: true }).click();
  const sent = page.waitForRequest("**/api/v1/analytics/bootstrap");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
  const payload = (await sent).postDataJSON();
  expect(payload.objective).toBe("gmv");
  expect(payload.target_return).toBe(0.08);
  expect(payload.bootstrap_objectives).toEqual(["target_return", "gmv"]);
});

test("App submits Resampled Frontier with a global Target Return and no target value", async ({
  page,
}) => {
  const manifest = JSON.parse(
    await readFile(resolve("public/demo/manifest.json"), "utf8"),
  );
  const result = JSON.parse(
    await readFile(resolve("public/demo/resampled-frontier.json"), "utf8"),
  );
  const request = {
    ...result.reproducibility.api_request,
    objective: "target_return",
    target_return: null,
    bootstrap_objectives: ["target_return"],
    bootstrap_iterations: 3,
  };
  result.reproducibility.api_request = request;
  await page.route("**/api/v1/analytics/catalog", (route) =>
    route.fulfill({ json: manifest.catalog }),
  );
  await page.route("**/api/v1/analytics/data-preview", (route) =>
    route.fulfill({ json: { period: result.period } }),
  );
  await page.route("**/api/v1/analytics/resampled-frontier", (route) =>
    route.fulfill({
      status: 422,
      json: { detail: "Payload captured for regression test" },
    }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("combobox", { name: "Метод в Demo", exact: true }),
  ).toBeEnabled();
  await page.locator("input[type=file]").setInputFiles({
    name: "resampled-request.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        schema: "qcross-portfolio-lab/v1",
        created_at: "test",
        instruments: manifest.catalog.instruments,
        calculations: [
          {
            id: "resampled-target",
            kind: "resampled-frontier",
            origin: "live",
            created_at: "test",
            request,
            result,
          },
        ],
      }),
    ),
  });
  await expect(page.getByText(/Снимок загружен локально/)).toBeVisible();
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page.getByRole("button", { name: "Bootstrap", exact: true }).click();
  const sent = page.waitForRequest("**/api/v1/analytics/resampled-frontier");
  await page
    .getByRole("button", { name: "Рассчитать Resampled Frontier", exact: true })
    .click();
  expect((await sent).postDataJSON()).toMatchObject({
    objective: "target_return",
    target_return: null,
  });
});
