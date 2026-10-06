import { describe, expect, it } from "vitest";
import {
  compareCalculations,
  compatible,
  createSnapshot,
  parseSnapshot,
} from "../lib/snapshot";
import { instruments, saved } from "./fixtures";
import type { FrontierResult } from "../types";

describe("full calculation snapshots", () => {
  it("round-trips user allocations, numbered groups and reference portfolio metrics", () => {
    const record = saved();
    const result = record.result as FrontierResult;
    result.user_portfolio = structuredClone(result.equal_weight);
    record.request.asset_groups = { "1": 1, "175": 3, "243": 5 };
    record.request.user_weights = structuredClone(
      result.user_portfolio.weights,
    );
    result.reproducibility.api_request = structuredClone(record.request);
    const snapshot = parseSnapshot(
      JSON.stringify(createSnapshot([record], instruments)),
    );
    expect(snapshot.calculations[0]).toEqual(record);
  });
  it("round-trips all four stored result schemas with provenance", () => {
    const records = [
      saved(),
      saved("cross-validation", "cross-validation.json"),
      saved("bootstrap", "bootstrap.json"),
      saved("resampled-frontier", "resampled-frontier.json"),
    ];
    expect(
      parseSnapshot(JSON.stringify(createSnapshot(records, instruments)))
        .calculations,
    ).toEqual(records);
  });
  it("rejects corrupted portfolio fields, absent source versions and mismatched requests", () => {
    const a = saved();
    (a.result as unknown as { gmv: unknown }).gmv = { weights: "bad" };
    expect(() =>
      parseSnapshot(JSON.stringify(createSnapshot([a], instruments))),
    ).toThrow();
    const b = saved();
    b.result.reproducibility.source_series[0].series_version = "unknown";
    expect(() =>
      parseSnapshot(JSON.stringify(createSnapshot([b], instruments))),
    ).toThrow();
    const c = saved();
    c.request.random_seed++;
    expect(() =>
      parseSnapshot(JSON.stringify(createSnapshot([c], instruments))),
    ).toThrow();
  });
  it("compares numerical tolerances, ignores timing and reports metadata drift", () => {
    const a = saved(),
      b = structuredClone(a);
    b.result.execution_seconds += 20;
    expect(compareCalculations(a, b, 1e-7, 1e-6).differences).toHaveLength(0);
    (
      b.result as unknown as { gmv: { weights: Record<string, number> } }
    ).gmv.weights["1"] += 0.01;
    b.result.reproducibility.returns_matrix_sha256 = "0".repeat(64);
    const c = compareCalculations(a, b, 1e-7, 1e-6);
    expect(c.differences.some((d) => d.path.endsWith("gmv.weights.1"))).toBe(
      true,
    );
    expect(c.warnings).toHaveLength(1);
  });
  it("does not skip bootstrap iteration allocations", () => {
    const a = saved("bootstrap", "bootstrap.json"),
      b = structuredClone(a);
    (
      b.result as unknown as {
        objectives: {
          gmv: { iterations: { weights: Record<string, number> }[] };
        };
      }
    ).objectives.gmv.iterations[0].weights["1"] += 0.1;
    expect(compareCalculations(a, b, 1e-7, 1e-6).differences[0].path).toContain(
      "iterations.0.weights",
    );
  });
  it("requires equal input settings and matrix identity for comparison", () => {
    const a = saved(),
      b = saved("frontier", "frontier-ledoit_wolf.json");
    expect(compatible(a, b)).toBe(true);
    b.request.group_constraints.Equity.max = 0.5;
    expect(compatible(a, b)).toBe(false);
  });
});
