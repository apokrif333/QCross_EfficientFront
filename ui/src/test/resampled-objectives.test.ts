import { afterEach, expect, it, vi } from "vitest";
import { requestSchemaFor } from "../lib/validation";
import { runJob } from "../lib/api";
import { createSnapshot, parseSnapshot } from "../lib/snapshot";
import { instruments, saved } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

it("requires a target only for operations that use it", () => {
  const request = {
    ...saved().request,
    objective: "target_return",
    target_return: null,
    bootstrap_objectives: ["target_return"],
    bootstrap_iterations: 3,
  };
  expect(
    requestSchemaFor("resampled-frontier").safeParse(request).success,
  ).toBe(true);
  for (const kind of ["frontier", "cross-validation", "bootstrap"] as const)
    expect(requestSchemaFor(kind).safeParse(request).success).toBe(false);
});

it("submits global Target Return without a target for Resampled Frontier", async () => {
  const request = {
    ...saved().request,
    objective: "target_return" as const,
    target_return: null,
    bootstrap_objectives: ["target_return" as const],
    bootstrap_iterations: 3,
  };
  const result = saved("resampled-frontier", "resampled-frontier.json").result;
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
    .mockResolvedValueOnce(new Response(JSON.stringify({ result })));
  vi.stubGlobal("fetch", fetch);
  await runJob(
    "resampled-frontier",
    request,
    vi.fn(),
    new AbortController().signal,
  );
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
    objective: "target_return",
    target_return: null,
  });
});

it("preserves explicit Target Return Bootstrap objective lists in API payloads", async () => {
  const request = {
    ...saved().request,
    objective: "gmv" as const,
    target_return: 0.08,
    bootstrap_objectives: ["target_return" as const, "gmv" as const],
    bootstrap_iterations: 3,
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
        JSON.stringify({ result: saved("bootstrap", "bootstrap.json").result }),
      ),
    );
  vi.stubGlobal("fetch", fetch);
  await runJob("bootstrap", request, vi.fn(), new AbortController().signal);
  expect(JSON.parse(fetch.mock.calls[0][1].body).bootstrap_objectives).toEqual([
    "target_return",
    "gmv",
  ]);
});

it("round-trips Resampled Frontier snapshots with global Target Return and no target", () => {
  const c = saved("resampled-frontier", "resampled-frontier.json");
  c.request.objective = "target_return";
  c.request.target_return = null;
  c.request.bootstrap_objectives = ["target_return"];
  c.result.reproducibility.api_request = structuredClone(c.request);
  const restored = parseSnapshot(
    JSON.stringify(createSnapshot([c], instruments)),
  );
  expect(restored.calculations[0].request.objective).toBe("target_return");
  expect(restored.calculations[0].request.target_return).toBeNull();
});
