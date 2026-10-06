import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { useState } from "react";
import Settings from "../components/Settings";
import AssetBuilder from "../components/AssetBuilder";
import PortfolioTable from "../components/PortfolioTable";
import {
  allocationError,
  rowsFromRequest,
  requestFromRows,
} from "../lib/allocations";
import { instruments, saved } from "./fixtures";
import type { FrontierResult } from "../types";

it("starts with five rows, searches grouped instruments and converts percent inputs", async () => {
  const onChange = vi.fn();
  function Harness() {
    const [rows, setRows] = useState(
      rowsFromRequest({
        ...saved().request,
        instrument_ids: [1, 175],
        asset_constraints: {},
      }),
    );
    return (
      <AssetBuilder
        rows={rows}
        instruments={instruments.map((i) => ({
          ...i,
          category: i.ticker === "GLD" ? "Commodity" : "US Stocks",
        }))}
        locked={false}
        onChange={(next) => {
          setRows(next);
          onChange(requestFromRows(next, saved().request));
        }}
      />
    );
  }
  render(<Harness />);
  const user = userEvent.setup();
  expect(screen.getAllByRole("combobox")).toHaveLength(10);
  await user.click(screen.getByLabelText("Тикер 3"));
  expect(screen.getByRole("group", { name: "Commodity" })).toBeVisible();
  await user.type(screen.getByLabelText("Тикер 3"), "GLD");
  expect(
    screen
      .getAllByRole("option")
      .filter((el) => el.closest('[role="listbox"]')),
  ).toHaveLength(1);
  await user.click(screen.getByRole("option", { name: /GLD/ }));
  await user.clear(screen.getByLabelText("Максимальный вес 1"));
  await user.type(screen.getByLabelText("Максимальный вес 1"), "40");
  expect(onChange.mock.calls.at(-1)![0].asset_constraints["1"].max).toBe(0.4);
  await user.type(screen.getByLabelText("Аллокация 1"), "100");
  expect(onChange.mock.calls.at(-1)![0].user_weights["1"]).toBe(1);
  await user.selectOptions(screen.getByLabelText("Группа актива 3"), "5");
  expect(onChange.mock.calls.at(-1)![0].asset_groups["243"]).toBe(5);
});

it("validates allocation totals and retains only used numbered group constraints", () => {
  const request = {
    ...saved().request,
    asset_groups: { "1": 1, "175": 3, "243": 5 },
  };
  const rows = rowsFromRequest(request);
  expect(rows).toHaveLength(5);
  expect(allocationError(rows)).toBe("");
  rows[0].allocation = "60";
  expect(allocationError(rows)).toContain("100% или 0%");
  rows[1].allocation = "40";
  expect(allocationError(rows)).toBe("");
  const updated = requestFromRows(rows, request);
  expect(Object.keys(updated.group_constraints)).toEqual([
    "Group 1",
    "Group 3",
    "Group 5",
  ]);
  expect(updated.user_weights).toEqual({ "1": 0.6, "175": 0.4, "243": 0 });
  rows[0].minimum = "50";
  rows[0].maximum = "40";
  expect(allocationError(rows)).toContain("Ограничения веса");
});

it("limits row count to fifteen and locks the builder in Demo", () => {
  const rows = rowsFromRequest({
    ...saved().request,
    instrument_ids: Array.from({ length: 15 }, (_, i) => i + 1),
  });
  const { rerender } = render(
    <AssetBuilder
      rows={rows}
      instruments={instruments}
      locked={false}
      onChange={vi.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: "Добавить актив" })).toBeDisabled();
  rerender(
    <AssetBuilder
      rows={rowsFromRequest(saved().request)}
      instruments={instruments}
      locked
      onChange={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Тикер 1")).toBeDisabled();
  expect(screen.getByLabelText("Аллокация 1")).toBeDisabled();
});

it("shows only used groups in settings and sends their bounds in weight units", async () => {
  const onChange = vi.fn();
  function Harness() {
    const [value, setValue] = useState({
      ...saved().request,
      asset_groups: { "1": 1, "175": 3, "243": 5 },
      group_constraints: {},
    });
    return (
      <Settings
        value={value}
        instruments={instruments}
        locked={false}
        onChange={(next) => {
          setValue(next as typeof value);
          onChange(next);
        }}
        onPreview={vi.fn()}
      />
    );
  }
  render(<Harness />);
  expect(screen.getByText("Группа 1", { exact: true })).toBeVisible();
  expect(screen.getByText("Группа 3", { exact: true })).toBeVisible();
  expect(screen.getByText("Группа 5", { exact: true })).toBeVisible();
  expect(
    screen.queryByText("Группа 2", { exact: true }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  const user = userEvent.setup();
  await user.clear(screen.getByLabelText("Группа 3 максимум %"));
  await user.type(screen.getByLabelText("Группа 3 максимум %"), "40");
  expect(onChange.mock.calls.at(-1)![0].group_constraints["Group 3"].max).toBe(
    0.4,
  );
});

it("labels references outside constraints and distinguishes expected return from CAGR", () => {
  const p = structuredClone((saved().result as FrontierResult).equal_weight);
  p.feasible = false;
  render(
    <PortfolioTable
      rows={[{ label: "Ваш портфель", portfolio: p }]}
      instruments={instruments}
    />,
  );
  expect(screen.getByText(/Вне ограничений/)).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: "Ожидаемая (арифм.)" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: "Историческая CAGR" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: /^Волатильность$/ }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "Историческая волатильность" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "Оценка волатильности" }),
  ).not.toBeInTheDocument();
});
