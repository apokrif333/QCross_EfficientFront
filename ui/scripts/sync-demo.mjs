import { mkdir, readFile, writeFile } from "node:fs/promises";
const source = new URL("../../docs/analytics-demo/", import.meta.url);
const output = new URL("../public/demo/", import.meta.url);
await mkdir(output, { recursive: true });
const records = [];
for (const [kind, file] of [
  ...["sample", "ledoit_wolf", "nonlinear", "cv_linear"].map((method) => [
    "frontier",
    `frontier-${method}.json`,
  ]),
  ["cross-validation", "cross-validation.json"],
  ["bootstrap", "bootstrap.json"],
  ["resampled-frontier", "resampled-frontier.json"],
]) {
  const text = await readFile(new URL(file, source), "utf8");
  const result = JSON.parse(text);
  if (!result.reproducibility?.api_request)
    throw new Error(
      `${file}: regenerate docs with scripts.analytics_demo first`,
    );
  await writeFile(new URL(file, output), text);
  records.push({
    id: `demo-${file}`,
    kind,
    request: result.reproducibility.api_request,
    origin: "demo",
    file,
  });
}
const first = JSON.parse(
  await readFile(new URL("frontier-sample.json", source), "utf8"),
);
const instruments = first.source_metadata.map((row) => ({
  id: row.instrument_id,
  ticker: row.ticker,
  name: row.ticker,
  category: null,
  groups: row.groups,
  series_id: row.series_id,
  start_date: row.start_date,
  end_date: row.end_date,
  observation_count: row.observation_count,
}));
await writeFile(
  new URL("manifest.json", output),
  JSON.stringify(
    { catalog: { engine: first.reproducibility.engine, instruments }, records },
    null,
    2,
  ),
);
console.log("Demo copied from stored USD calculations; no database access.");
