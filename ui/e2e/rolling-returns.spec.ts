import { expect, test } from "@playwright/test";

test("repeated Frontier runs replace the rolling returns chart", async ({
  page,
}) => {
  test.skip(
    !process.env.QCROSS_LIVE_TEST,
    "Requires the local stored-history API.",
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Безрисковая доходность", exact: true })
    .selectOption("fixed_annual");
  await page
    .getByRole("combobox", { name: "Метод ковариации", exact: true })
    .selectOption("sample");
  await page.getByLabel("Аллокация 1", { exact: true }).fill("100");
  await page.getByLabel("Начало", { exact: true }).fill("2000-01-01");

  const rolling = page.getByRole("region", { name: "Скользящая доходность" });
  const plots = rolling
    .getByRole("img", { name: "Годовая скользящая доходность" })
    .locator(".js-plotly-plot");
  for (const end of ["2020-12-31", "2018-12-31"]) {
    const endField = page.getByLabel("Конец", { exact: true });
    await endField.fill(end);
    await endField.press("Escape");
    const response = page.waitForResponse(
      async (response) =>
        /\/jobs\/[^/]+\/result$/.test(response.url()) &&
        (await response.json()).status === "completed",
    );
    await page.getByRole("button", { name: "Рассчитать", exact: true }).click();
    const { result } = await (await response).json();
    await expect
      .poll(() =>
        plots.evaluateAll((elements) =>
          elements.map((el) =>
            (el as unknown as { data?: { x: string[] }[] }).data?.[0].x.at(-1),
          ),
        ),
      )
      .toEqual([result.period.end]);
    await expect(rolling).toHaveCount(1);
    await rolling.getByLabel("Горизонт, лет").fill("3");
    const positive = await rolling.getByText(/^Положительные:/).innerText();
    const traces = await page
      .getByRole("img", { name: "Efficient Frontier", exact: true })
      .locator(".js-plotly-plot")
      .evaluate((el) => {
        const plot = el as unknown as {
          data: {
            name: string;
            hovertemplate: string[];
            customdata: {
              metrics: { rolling_3y: { positive: number; total: number } };
            }[];
          }[];
        };
        return plot.data;
      });
    expect(
      traces.find(({ name }) => name === "Ваш портфель")?.hovertemplate[0],
    ).toContain(positive.replace("Положительные:", "Положительные (3 года):"));
    expect(
      traces.find(({ name }) => name === "Активы")?.customdata,
    ).toHaveLength(result.assets.length);
    for (const trace of traces) {
      for (const [index, point] of trace.customdata.entries()) {
        const { positive, total } = point.metrics.rolling_3y;
        expect(total).toBe(result.period.observations - 35);
        expect(trace.hovertemplate[index]).toContain(
          `Положительные (3 года): ${positive} (${((positive / total) * 100).toFixed(2)}%)`,
        );
      }
    }
    await rolling.getByLabel("Портфель").selectOption("equal");
    await expect(rolling).toHaveCount(1);
  }
});
