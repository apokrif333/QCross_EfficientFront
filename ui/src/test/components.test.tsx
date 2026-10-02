import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { useState } from "react";
import Settings from "../components/Settings";
import PortfolioTable from "../components/PortfolioTable";
import { instruments, saved } from "./fixtures";
import type { FrontierResult } from "../types";

it("searches instruments and sends weight units and CV controls to the parent", async () => {
  const onChange = vi.fn();
  function Harness() {
    const [value, setValue] = useState(saved().request);
    return (
      <Settings
        value={value}
        instruments={instruments}
        locked={false}
        onChange={(v) => {
          setValue(v);
          onChange(v);
        }}
        onPreview={vi.fn()}
      />
    );
  }
  render(<Harness />);
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText("Тикер или название"), "GLD");
  expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  expect(screen.getByText(/Общая история/)).toBeInTheDocument();
  const max = screen.getByLabelText("VTI максимум %");
  await user.clear(max);
  await user.type(max, "40");
  expect(onChange.mock.calls.at(-1)![0].asset_constraints["1"].max).toBe(0.4);
});
it("limits selections to fifteen and locks settings in Demo", () => {
  const assets = Array.from({ length: 16 }, (_, i) => ({
    ...instruments[0],
    id: i + 1,
    ticker: `ETF${i}`,
  }));
  render(
    <Settings
      value={{
        ...saved().request,
        instrument_ids: assets.slice(0, 15).map((a) => a.id),
      }}
      instruments={assets}
      locked={false}
      onChange={vi.fn()}
      onPreview={vi.fn()}
    />,
  );
  expect(screen.getAllByRole("checkbox")[15]).toBeDisabled();
});
it("labels equal weight as an unconstrained reference when infeasible", () => {
  const p = structuredClone((saved().result as FrontierResult).equal_weight);
  p.feasible = false;
  render(
    <PortfolioTable
      rows={[{ label: "Equal Weight", portfolio: p }]}
      instruments={instruments}
    />,
  );
  expect(screen.getByText(/Вне ограничений/)).toBeInTheDocument();
});
