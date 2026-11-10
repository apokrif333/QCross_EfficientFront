import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import Settings from "../components/Settings";
import { CV_FOLDS, cvHistoryError, minimumCVHistory } from "../lib/cv";
import { requestSchema } from "../lib/validation";
import { runJob } from "../lib/api";
import { DEFAULT_REQUEST } from "../types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("defaults to four, accepts only supported even K and checks both minimum tables", () => {
  expect(DEFAULT_REQUEST.cv_folds).toBe(4);
  expect(DEFAULT_REQUEST.covariance_method).toBe("nonlinear");
  for (const k of CV_FOLDS) {
    expect(
      requestSchema.safeParse({ ...DEFAULT_REQUEST, cv_folds: k }).success,
    ).toBe(true);
    expect(minimumCVHistory(k)).toBe(k === 2 ? 180 : k * 60);
    expect(minimumCVHistory(k, true)).toBe(k === 2 ? 90 : k * 30);
  }
  for (const k of [0, 1, 3, 5, 19, 21]) {
    expect(
      requestSchema.safeParse({ ...DEFAULT_REQUEST, cv_folds: k }).success,
    ).toBe(false);
  }
});

it("validates history for the requested operation", () => {
  expect(cvHistoryError(DEFAULT_REQUEST, "frontier", 120)).toBeNull();
  expect(cvHistoryError(DEFAULT_REQUEST, "cross-validation", 120)).toContain(
    "240",
  );
  const covariance = {
    ...DEFAULT_REQUEST,
    covariance_method: "cv_linear" as const,
  };
  expect(cvHistoryError(covariance, "frontier", 120)).toBeNull();
  expect(
    cvHistoryError({ ...covariance, cv_folds: 6 }, "frontier", 120),
  ).toContain("180");
  expect(
    cvHistoryError({ ...covariance, cv_folds: 2 }, "cross-validation", 180),
  ).toBeNull();
});

it("offers only even folds and disables unavailable portfolio folds using actual history", async () => {
  const onChange = vi.fn();
  render(
    <Settings
      value={DEFAULT_REQUEST}
      instruments={[]}
      locked={false}
      onChange={onChange}
      onPreview={vi.fn()}
      observations={180}
      portfolioCV
    />,
  );
  const select = screen.getByLabelText("Число CV-фолдов");
  expect(
    Array.from(select.querySelectorAll("option")).map((o) => Number(o.value)),
  ).toEqual(CV_FOLDS);
  expect(
    Array.from(select.querySelectorAll("option")).map((o) =>
      o.textContent?.trim(),
    ),
  ).toEqual(CV_FOLDS.map(String));
  expect(select.querySelector('option[value="2"]')).not.toBeDisabled();
  expect(select.querySelector('option[value="4"]')).toBeDisabled();
  await userEvent.selectOptions(select, "2");
  expect(onChange.mock.calls[0][0].cv_folds).toBe(2);
});

it("keeps four-fold internal covariance CV available with 120 months", () => {
  render(
    <Settings
      value={{ ...DEFAULT_REQUEST, covariance_method: "cv_linear" }}
      instruments={[]}
      locked={false}
      onChange={vi.fn()}
      onPreview={vi.fn()}
      observations={120}
    />,
  );
  const select = screen.getByLabelText("Число CV-фолдов");
  expect(select.querySelector('option[value="4"]')).not.toBeDisabled();
  expect(select.querySelector('option[value="6"]')).toBeDisabled();
});

it("rejects odd K before submitting an API job", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  await expect(
    runJob(
      "frontier",
      { ...DEFAULT_REQUEST, cv_folds: 5 },
      vi.fn(),
      new AbortController().signal,
    ),
  ).rejects.toThrow();
  expect(fetch).not.toHaveBeenCalled();
});
