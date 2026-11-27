import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import BootstrapCalibration from "../components/BootstrapCalibration";
import { previewCalibration, runCalibration } from "../lib/api";
import type { CalibrationResult } from "../lib/calibration";
import { DEFAULT_REQUEST } from "../types";

vi.mock("../lib/api", () => ({
  previewCalibration: vi.fn(),
  runCalibration: vi.fn(),
  fetchCalibrationDetail: vi.fn(),
  cancelCalibration: vi.fn(),
  requestJson: vi.fn(),
}));

vi.mock("../components/Plot", () => ({
  default: ({ title }: { title: string }) => (
    <div role="img" aria-label={title} />
  ),
}));

const train = { start: "1940-01", end: "1959-12", months: 240 };
const test = { start: "1960-01", end: "1979-12", months: 240 };
const preview = {
  ready: true,
  history: { ...test, start: train.start, months: 720 },
  folds: [{ fold: 1, train, test, holdout: false, intervals: { "12": 20 } }],
  holdout: {
    fold: 2,
    train: test,
    test: { ...test, start: "1980-01", end: "1999-12" },
    holdout: true,
    intervals: { "12": 20 },
  },
  full_calibration_folds: 1,
  lengths: [1, 12],
  horizons: [12],
  interval_counts: { "12": 20 },
  generations: 1000,
  trajectory_count: 2000,
  warnings: [],
  assets: [
    { instrument_id: 1, ticker: "VTI" },
    { instrument_id: 2, ticker: "TLT" },
  ],
};
const score = {
  energy: 0.16,
  temporal_returns: 0.17,
  temporal_absolute: 0.18,
  cross_asset: 0.19,
};
const pit = {
  count: 10,
  histogram: Array<number>(10).fill(1),
  p05_p95_coverage: 0.9,
  p25_p75_coverage: 0.5,
  median_absolute_deviation: 0.25,
};
const result: CalibrationResult = {
  methodology: "chronological_bootstrap_block_length_calibration_v1",
  phase: "calibration",
  holdout_status: "available",
  preview,
  optimal_length: 1,
  minimum_score: 0.2,
  ranking: [
    {
      length: 1,
      score: 0.2,
      relative_difference: 0,
      comparable: true,
      ...score,
    },
    {
      length: 12,
      score: 0.4,
      relative_difference: 1,
      comparable: false,
      ...score,
    },
  ],
  rows: [],
  group_weights: { returns: 0.2 },
  horizon_weights: { "12": 1 },
  by_horizon: { "12": { "1": 0.2, "12": 0.4 } },
  by_fold: {},
  by_group: {},
  by_metric: {},
  best_by_horizon: { "12": 1 },
  best_by_group: {},
  sensitivity: {},
  pit: { "1": pit, "12": pit },
  metrics: {},
  runs: { "1": { "12": { compounded_return: [["1"]] } } },
  execution_seconds: 0.1,
  reproducibility: { calculation_sha256: "a" },
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

it("previews the chronological plan, runs Calibration and changes views without rerunning", async () => {
  vi.mocked(previewCalibration).mockResolvedValue(preview);
  let finish: (() => void) | undefined;
  vi.mocked(runCalibration).mockImplementation(async (body, report) => {
    const holdout = "calibration_job_id" in body;
    if (!holdout) {
      report({
        job_id: "calibration",
        kind: "bootstrap-calibration",
        status: "running",
        result_url: "",
        status_url: "",
        timeout_seconds: 600,
        elapsed_seconds: 306,
        error: null,
        progress: {
          phase: "scoring",
          completed_steps: 120,
          total_steps: 231,
          fold_index: 6,
          total_folds: 10,
          block_length: 7,
        },
      });
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
    }
    report({
      job_id: holdout ? "holdout" : "calibration",
      kind: holdout ? "bootstrap-calibration-holdout" : "bootstrap-calibration",
      status: "completed",
      result_url: "",
      status_url: "",
      timeout_seconds: 10,
      elapsed_seconds: 1,
      error: null,
    });
    return holdout
      ? {
          ...result,
          phase: "holdout",
          holdout_status: "evaluated",
          ranking: result.ranking.slice(0, 1),
        }
      : result;
  });
  const onUseLength = vi.fn();
  render(
    <BootstrapCalibration
      request={{ ...DEFAULT_REQUEST, instrument_ids: [1, 2] }}
      mode="live"
      instruments={[]}
      onUseLength={onUseLength}
    />,
  );
  const run = screen.getByRole("button", { name: "Запустить Calibration" });
  expect(run).toBeDisabled();
  const minimum = screen.getByRole("spinbutton", {
    name: "Минимальная длина блока",
  });
  expect(minimum).toHaveAttribute("min", "1");
  fireEvent.change(minimum, { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Проверить периоды" }));
  await waitFor(() => expect(run).toBeEnabled());
  expect(previewCalibration).toHaveBeenCalledWith(
    expect.objectContaining({
      instrument_ids: [1, 2],
      start_date: null,
      train_length: 240,
      test_length: 240,
      min_block_length: 1,
      generations: 1000,
      horizons: [3, 6, 12, 24, 60, 120, 240],
      final_holdout: true,
    }),
  );
  fireEvent.click(run);
  expect(
    screen.getByRole("progressbar", { name: "Прогресс Calibration" }),
  ).toHaveAttribute("value", "51");
  expect(screen.getByRole("status")).toHaveTextContent(
    "Fold 6 из 10 · Оцениваем длину блока L=7 мес.",
  );
  expect(screen.getByRole("button", { name: "Отменить задачу" })).toBeEnabled();
  await act(async () => finish?.());
  await screen.findByRole("img", { name: "Aggregated Normalized CRPS" });
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Критерий графика"), {
    target: { value: "energy" },
  });
  expect(screen.getByRole("img", { name: "Energy Score" })).toBeInTheDocument();
  expect(runCalibration).toHaveBeenCalledTimes(1);
  expect(onUseLength).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("Выбранная длина блока"), {
    target: { value: "1" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Use Selected Block Length" }),
  );
  expect(onUseLength).toHaveBeenCalledWith(1);
  fireEvent.click(
    screen.getByRole("button", { name: "Запустить Final Holdout" }),
  );
  await screen.findByRole("heading", { name: "Final Holdout" });
  expect(runCalibration).toHaveBeenLastCalledWith(
    { calibration_job_id: "calibration" },
    expect.any(Function),
    expect.any(AbortSignal),
  );
  expect(
    screen.getByText(/Проверяется L=1, выбранное на Calibration/),
  ).toBeVisible();
});
