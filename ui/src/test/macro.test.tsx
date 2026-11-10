import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import Settings from "../components/Settings";
import { DEFAULT_REQUEST } from "../types";
import {
  compatible,
  settingsKey,
  createSnapshot,
  parseSnapshot,
} from "../lib/snapshot";
import { saved, instruments } from "./fixtures";

afterEach(cleanup);
it("defaults to nominal historical BIL and hides the manual rate", async () => {
  expect(DEFAULT_REQUEST.return_basis).toBe("nominal");
  expect(DEFAULT_REQUEST.risk_free_source).toBe("historical_bil");
  const onChange = vi.fn();
  render(
    <Settings
      value={DEFAULT_REQUEST}
      instruments={[]}
      locked={false}
      onChange={onChange}
      onPreview={vi.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: "До инфляции" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(
    screen.queryByLabelText("Безрисковая ставка, %"),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "После инфляции" }));
  expect(onChange.mock.calls[0][0].return_basis).toBe("real");
});
it("preserves old fixed annual snapshot semantics and rejects incompatible overlays", () => {
  const old = saved();
  const restored = parseSnapshot(
    JSON.stringify(createSnapshot([old], instruments)),
  ).calculations[0];
  expect(restored.request.risk_free_source).toBeUndefined();
  const fixed = structuredClone(old);
  fixed.request.return_basis = "nominal";
  fixed.request.risk_free_source = "fixed_annual";
  expect(settingsKey(fixed.request)).toBe(settingsKey(old.request));
  expect(compatible(old, fixed)).toBe(true);
  const bil = structuredClone(fixed);
  bil.request.risk_free_source = "historical_bil";
  expect(compatible(fixed, bil)).toBe(false);
  const real = structuredClone(fixed);
  real.request.return_basis = "real";
  expect(compatible(fixed, real)).toBe(false);
  const revised = structuredClone(fixed);
  revised.result.reproducibility.reference_panel_sha256 = "a".repeat(64);
  expect(compatible(fixed, revised)).toBe(false);
});
it("shows the manual rate for explicit fixed mode", () => {
  render(
    <Settings
      value={{ ...DEFAULT_REQUEST, risk_free_source: "fixed_annual" }}
      instruments={[]}
      locked={false}
      onChange={vi.fn()}
      onPreview={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Безрисковая ставка, %")).toHaveValue(3);
});
