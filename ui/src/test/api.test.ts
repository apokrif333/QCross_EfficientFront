import { afterEach, expect, it, vi } from "vitest";
import { requestJson, runJob } from "../lib/api";
import { saved } from "./fixtures";
afterEach(() => vi.unstubAllGlobals());
const reply = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
it("surfaces validation and capacity errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(reply({ detail: "infeasible allocations" }, 422)),
  );
  await expect(requestJson("/api/test")).rejects.toThrow(
    "infeasible allocations",
  );
});
it("never produces a result for failed or timed-out jobs", async () => {
  for (const status of ["failed", "timed_out"]) {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          reply({ status, error: "solver failed", timeout_seconds: 300 }, 202),
        ),
    );
    await expect(
      runJob(
        "frontier",
        saved().request,
        vi.fn(),
        new AbortController().signal,
      ),
    ).rejects.toThrow("solver failed");
  }
});
it("returns incomplete responses honestly", async () => {
  const result = { ...saved().result, status: "incomplete" };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        reply(
          { status: "completed", result_url: "/result", timeout_seconds: 300 },
          202,
        ),
      )
      .mockResolvedValueOnce(reply({ result })),
  );
  expect(
    (
      await runJob(
        "frontier",
        saved().request,
        vi.fn(),
        new AbortController().signal,
      )
    ).status,
  ).toBe("incomplete");
});
it("reports backend outage without a fallback success", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("network error")),
  );
  await expect(requestJson("/api/test")).rejects.toThrow(/недоступен/);
});
