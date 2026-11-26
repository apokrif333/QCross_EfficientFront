import { afterEach, expect, it, vi } from "vitest";
import { reusableControlCache } from "../lib/controlCache";
import { runJob } from "../lib/api";
import {
  ANNUAL_TAX_METHODOLOGY,
  createSnapshot,
  parseSnapshot,
} from "../lib/snapshot";
import type { BootstrapControlCache, BootstrapResult } from "../types";
import { instruments, saved } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());
const cache: BootstrapControlCache = {
  version: 1,
  tax_methodology: ANNUAL_TAX_METHODOLOGY,
  key: "a".repeat(64),
  original: {},
  samples: [
    {
      indices_sha256: "b".repeat(64),
      expected_return: 0.06,
      volatility: 0.1,
      error: null,
    },
  ],
};

it("reuses saved Bootstrap control summaries after snapshot import", () => {
  const calculation = saved("bootstrap", "bootstrap.json");
  (calculation.result as BootstrapResult).portfolio_control_cache = cache;
  const restored = parseSnapshot(
    JSON.stringify(createSnapshot([calculation], instruments)),
  );
  expect(
    reusableControlCache(
      { ...calculation.request, bootstrap_iterations: 250 },
      restored.calculations,
    ),
  ).toEqual(cache);
  const restoredCache = (restored.calculations[0].result as BootstrapResult)
    .portfolio_control_cache!;
  delete restoredCache.tax_methodology;
  expect(
    reusableControlCache(calculation.request, restored.calculations),
  ).toBeUndefined();
  restoredCache.tax_methodology = ANNUAL_TAX_METHODOLOGY;
  for (const changed of [
    { random_seed: 123 },
    { expected_block_length: 6 },
    { return_basis: "real" as const },
    { transaction_fee_rate: 0.01 },
    { capital_gains_tax_rate: 0.1 },
    { user_weights: { "1": 1 } },
  ])
    expect(
      reusableControlCache(
        { ...calculation.request, ...changed },
        restored.calculations,
      ),
    ).toBeUndefined();
});

it("sends the optional cache only with a Resampled Frontier request", async () => {
  const body = {
    ...saved().request,
    bootstrap_iterations: 250,
    capital_gains_tax_rate: 0.1,
  };
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          status: "completed",
          result_url: "/result",
          timeout_seconds: 300,
        }),
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          result: saved("resampled-frontier", "resampled-frontier.json").result,
        }),
      ),
    );
  vi.stubGlobal("fetch", fetch);
  await runJob(
    "resampled-frontier",
    body,
    () => {},
    new AbortController().signal,
    cache,
  );
  expect(
    JSON.parse(fetch.mock.calls[0][1].body).bootstrap_control_cache,
  ).toEqual(cache);
  expect(JSON.parse(fetch.mock.calls[0][1].body).capital_gains_tax_rate).toBe(
    0.1,
  );
});
