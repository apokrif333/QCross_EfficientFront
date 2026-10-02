import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Calculation, Instrument } from "../types";
export function saved(
  kind = "frontier",
  file = "frontier-sample.json",
): Calculation {
  const result = JSON.parse(
    readFileSync(
      resolve(process.cwd(), "../docs/analytics-demo", file),
      "utf8",
    ),
  );
  return {
    id: "test-" + file,
    kind: kind as Calculation["kind"],
    request: structuredClone(result.reproducibility.api_request),
    result,
    origin: "demo",
    created_at: "saved-demo",
  };
}
export const instruments: Instrument[] = saved().result.source_metadata.map(
  (i) => ({
    ...i,
    name: i.ticker,
    category: null,
    id: i.instrument_id,
    observation_count: 440,
  }),
);
