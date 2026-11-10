import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import Settings from "../components/Settings";
import { BootstrapView } from "../components/Results";
import { DEFAULT_REQUEST, type BootstrapResult } from "../types";
import { requestSchema } from "../lib/validation";
import { createSnapshot, parseSnapshot } from "../lib/snapshot";
import { saved, instruments } from "./fixtures";

vi.mock("../components/Plot", () => ({
  default: () => <div data-testid="plot" />,
}));
afterEach(cleanup);

function targetCalculation() {
  const c = saved("bootstrap", "bootstrap.json");
  const r = c.result as BootstrapResult;
  const o = r.objectives.gmv;
  r.objectives = {
    target_return: {
      ...o,
      target_return: 0.08,
      target_feasibility_rate: 0.82,
      computational_status: "complete",
      requested_iterations: 1000,
      target_feasible_iterations: 820,
      target_infeasible_iterations: 180,
      numerical_failure_iterations: 0,
      failed_iterations: 0,
      successful_iterations: 820,
      original_target_feasible: true,
      resampled_target_evaluation: {
        expected_return: 0.07,
        target_return: 0.08,
        target_shortfall: 0.01,
        target_surplus: 0,
        meets_target: false,
        evaluation: "original_full_sample_estimates",
        target_guaranteed: false,
      },
    },
  };
  c.request.objective = "target_return";
  c.request.bootstrap_objectives = ["target_return"];
  c.request.target_return = 0.08;
  r.reproducibility.api_request = structuredClone(c.request);
  return c;
}

it("uses the existing target input and selects Target Return Bootstrap", async () => {
  const change = vi.fn();
  render(
    <Settings
      value={DEFAULT_REQUEST}
      instruments={[]}
      locked={false}
      onChange={change}
      onPreview={vi.fn()}
    />,
  );
  await userEvent.selectOptions(
    screen.getByLabelText("Цель оптимизации"),
    "target_return",
  );
  const next = change.mock.calls.at(-1)![0];
  expect(next.target_return).toBe(0.08);
  expect(next.bootstrap_objectives).toEqual(["target_return"]);
  expect(requestSchema.safeParse(next).success).toBe(true);
  expect(
    requestSchema.safeParse({ ...next, target_return: null }).success,
  ).toBe(false);
  expect(
    requestSchema.safeParse({
      ...next,
      bootstrap_objectives: ["target_return", "target_return"],
    }).success,
  ).toBe(false);
  expect(
    requestSchema.safeParse({
      ...next,
      bootstrap_objectives: ["target_return", "gmv", "max_sharpe"],
    }).success,
  ).toBe(true);
});

it("shows feasibility, separate failures and original-sample shortfall", () => {
  const c = targetCalculation();
  render(
    <BootstrapView
      result={c.result as BootstrapResult}
      instruments={instruments}
    />,
  );
  expect(
    screen.getByText(/Достижимость целевой доходности в Bootstrap: 82/),
  ).toBeVisible();
  expect(
    screen.getByText(/недостижима 180 · численных ошибок 0/),
  ).toBeVisible();
  expect(screen.getByText(/цель не выполнена/)).toBeVisible();
  expect(screen.getByText(/только по успешным выборкам/)).toBeVisible();
  expect(screen.getByText("P95")).toBeVisible();
  expect(
    screen.getByTitle(/Не является вероятностью будущей доходности/),
  ).toHaveAttribute("title");
});

it("round-trips target snapshots including absent original and zero feasible samples", () => {
  const c = targetCalculation();
  const o = (c.result as BootstrapResult).objectives.target_return;
  o.original = {
    status: "target_infeasible",
    weights: null,
    metrics: null,
    diagnostics: {},
  };
  o.original_target_feasible = false;
  for (const s of Object.values(o.stability!.assets)) s.original = null;
  o.stability!.allocation_distance = null;
  expect(() =>
    parseSnapshot(JSON.stringify(createSnapshot([c], instruments))),
  ).not.toThrow();
  o.target_feasibility_rate = 0;
  o.target_feasible_iterations = o.successful_iterations = 0;
  o.target_infeasible_iterations = 1000;
  o.resampled = o.stability = o.resampled_target_evaluation = null;
  const restored = parseSnapshot(
    JSON.stringify(createSnapshot([c], instruments)),
  );
  expect(
    (restored.calculations[0].result as BootstrapResult).objectives
      .target_return.resampled,
  ).toBeNull();
  render(
    <BootstrapView
      result={c.result as BootstrapResult}
      instruments={instruments}
    />,
  );
  expect(screen.getByText(/Исходного целевого портфеля нет/)).toBeVisible();
  expect(screen.queryByText("P95")).not.toBeInTheDocument();
});
