import { useId, useRef, useState } from "react";
import type { Instrument } from "../types";
import { allocationError, emptyRow, type AssetRow } from "../lib/allocations";

function InstrumentPicker({
  value,
  instruments,
  taken,
  locked,
  onSelect,
  index,
}: {
  value: number | null;
  instruments: Instrument[];
  taken: number[];
  locked: boolean;
  onSelect: (id: number | null) => void;
  index: number;
}) {
  const [open, setOpen] = useState(false),
    [query, setQuery] = useState(""),
    [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null),
    listId = useId();
  const selected = instruments.find((i) => i.id === value);
  const filtered = instruments.filter((i) =>
    `${i.ticker} ${i.name ?? ""} ${i.category ?? ""}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const ordered = [...filtered].sort(
    (a, b) =>
      (a.category ?? "Другие").localeCompare(b.category ?? "Другие") ||
      a.ticker.localeCompare(b.ticker),
  );
  const choose = (id: number) => {
    onSelect(id);
    setOpen(false);
    setQuery("");
  };
  return (
    <div
      className="instrument-picker"
      ref={root}
      onBlur={(e) => {
        if (!root.current?.contains(e.relatedTarget)) {
          setOpen(false);
          setQuery("");
        }
      }}
    >
      <div className="picker-input">
        <input
          role="combobox"
          aria-label={`Тикер ${index + 1}`}
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={
            open && ordered[active]
              ? `${listId}-${ordered[active].id}`
              : undefined
          }
          disabled={locked}
          placeholder="Выбрать инструмент…"
          value={open ? query : (selected?.ticker ?? "")}
          onClick={() => {
            if (!open) {
              setQuery("");
              setOpen(true);
            }
          }}
          onFocus={() => {
            setOpen(true);
            setQuery("");
            setActive(0);
          }}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setOpen(false);
              setQuery("");
            }
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setActive((a) => Math.min(a + 1, ordered.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(0, a - 1));
            }
            if (e.key === "Enter" && open && ordered[active]) {
              e.preventDefault();
              const i = ordered[active];
              if (!taken.includes(i.id) || i.id === value) choose(i.id);
            }
          }}
        />
        {value !== null && !locked && (
          <button
            type="button"
            aria-label={`Очистить актив ${index + 1}`}
            className="clear-asset"
            onClick={() => {
              onSelect(null);
              setOpen(false);
              setQuery("");
            }}
          >
            ×
          </button>
        )}
      </div>
      <small>{selected?.name ?? "Тикер, название или категория"}</small>
      {open && (
        <div
          id={listId}
          role="listbox"
          aria-label={`Инструменты строки ${index + 1}`}
          className="instrument-menu"
        >
          {ordered.length === 0 && (
            <p className="hint">Инструменты не найдены.</p>
          )}
          {[...new Set(ordered.map((i) => i.category ?? "Другие"))].map(
            (category) => (
              <div role="group" aria-label={category} key={category}>
                <div className="instrument-category">{category}</div>
                {ordered
                  .filter((i) => (i.category ?? "Другие") === category)
                  .map((i) => {
                    const disabled = taken.includes(i.id) && i.id !== value;
                    return (
                      <button
                        type="button"
                        role="option"
                        id={`${listId}-${i.id}`}
                        aria-selected={i.id === value}
                        aria-disabled={disabled}
                        disabled={disabled}
                        key={i.id}
                        className={`instrument-option ${ordered[active]?.id === i.id ? "highlighted" : ""}`}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => choose(i.id)}
                      >
                        <b>{i.ticker}</b>
                        <span>{i.name ?? i.ticker}</span>
                        <small>
                          {i.start_date.slice(0, 7)} — {i.end_date.slice(0, 7)}{" "}
                          · USD{disabled ? " · уже выбран" : ""}
                        </small>
                      </button>
                    );
                  })}
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}
export default function AssetBuilder({
  rows,
  instruments,
  locked,
  onChange,
}: {
  rows: AssetRow[];
  instruments: Instrument[];
  locked: boolean;
  onChange: (rows: AssetRow[]) => void;
}) {
  const selected = rows.filter((r) => r.instrumentId !== null),
    total = selected.reduce((n, r) => n + Number(r.allocation || 0), 0),
    error = allocationError(rows);
  const update = (key: string, patch: Partial<AssetRow>) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  return (
    <section
      className="asset-builder"
      aria-label="Активы и пользовательский портфель"
    >
      <div className="section-heading">
        <div>
          <h2>Активы и ваш портфель</h2>
          <p>
            2–15 инструментов · USD · аллокация 100% или 0% для расчёта без
            пользовательского портфеля
          </p>
        </div>
        <span className={`allocation-total ${error ? "invalid" : ""}`}>
          Аллокация: {total.toFixed(2)}%
        </span>
      </div>
      <div className="asset-grid asset-grid-heading">
        <span>Тикер</span>
        <span>Аллокация, %</span>
        <span>Минимальный вес, %</span>
        <span>Максимальный вес, %</span>
        <span>Группа</span>
        <span />
      </div>
      {rows.map((r, index) => (
        <div className="asset-grid" key={r.key}>
          <InstrumentPicker
            index={index}
            value={r.instrumentId}
            instruments={instruments}
            taken={selected.map((r) => r.instrumentId!)}
            locked={locked}
            onSelect={(instrumentId) =>
              update(r.key, {
                instrumentId,
                allocation: "",
                minimum: "0",
                maximum: "100",
              })
            }
          />
          {(["allocation", "minimum", "maximum"] as const).map((field, k) => (
            <label className="row-field" key={field}>
              <span>
                {["Аллокация", "Минимальный вес", "Максимальный вес"][k]}, %
              </span>
              <input
                aria-label={`${["Аллокация", "Минимальный вес", "Максимальный вес"][k]} ${index + 1}`}
                type="number"
                min="0"
                max="100"
                step="any"
                disabled={locked}
                placeholder={field === "allocation" ? "Не задана" : undefined}
                value={r[field]}
                onChange={(e) => update(r.key, { [field]: e.target.value })}
              />
            </label>
          ))}
          <label className="row-field">
            <span>Группа</span>
            <select
              aria-label={`Группа актива ${index + 1}`}
              disabled={locked}
              value={r.group}
              onChange={(e) => update(r.key, { group: +e.target.value })}
            >
              {[1, 2, 3, 4, 5].map((g) => (
                <option value={g} key={g}>
                  {g}
                </option>
              ))}
            </select>
          </label>
          {rows.length > 5 && (
            <button
              type="button"
              className="text-button remove-row"
              disabled={locked}
              aria-label={`Удалить строку ${index + 1}`}
              onClick={() => onChange(rows.filter((row) => row.key !== r.key))}
            >
              ×
            </button>
          )}
        </div>
      ))}
      <div className="asset-builder-footer">
        <button
          type="button"
          className="secondary"
          disabled={locked || rows.length >= 15}
          onClick={() => onChange([...rows, emptyRow()])}
        >
          Добавить актив
        </button>
        <small>
          {rows.length} / 15 строк · выбрано {selected.length}
        </small>
      </div>
      {error && (
        <p className="warning" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
