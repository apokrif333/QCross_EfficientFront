import { Fragment, useId, useMemo, useState } from "react";
import type { Data } from "plotly.js";
import type { Instrument, PairedComparison } from "../types";
import { number, percent, ticker, visibleWeights } from "../lib/display";
import Plot from "./Plot";

const columns = [
  ["CAGR", "annualized_cagr", percent],
  ["Vol", "annualized_volatility", percent],
  ["DD", "maximum_drawdown", percent],
  ["Sharpe", "historical_sharpe_ratio", number],
] as const;
type MetricKey = (typeof columns)[number][1];
type SortKey = "pair" | "weights" | MetricKey;

function wealthSeries(encoded: string) {
  const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  return Array.from({ length: bytes.length / 4 }, (_, month) => {
    const log = view.getFloat32(month * 4, true);
    return Number.isFinite(log)
      ? Math.max(
          Number.MIN_VALUE,
          Math.exp(Math.min(log, Math.log(Number.MAX_VALUE))),
        )
      : null;
  });
}

function ValidationChart({
  pair,
  paths,
  userLabel,
}: {
  pair: number;
  paths: NonNullable<PairedComparison["results"][number]["wealth_log_f32"]>;
  userLabel: string;
}) {
  const data: Data[] = [];
  for (const [key, name, color] of [
    ["user", userLabel, "#197b83"],
    ["optimized", "Оптимизированный портфель", "#b34b71"],
  ] as const) {
    if (!paths[key]) continue;
    const y = wealthSeries(paths[key]);
    data.push({
      type: "scatter",
      mode: "lines",
      name,
      y,
      x: y.map((_, month) => month),
      line: { color },
      hovertemplate: "%{y:.4g}<extra>%{fullData.name}</extra>",
    });
  }
  return (
    <Plot
      title={`Капитал на Validation №${pair}`}
      data={data}
      height={340}
      layout={{
        hovermode: "x unified",
        xaxis: { title: { text: "Месяц выборки" } },
        yaxis: {
          type: "log",
          title: { text: "Капитал из $1 после комиссий (лог.)" },
        },
      }}
    />
  );
}

export default function BootstrapValidationTable({
  value,
  instruments,
}: {
  value: PairedComparison;
  instruments: Instrument[];
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; direction: "asc" | "desc" }>(
    {
      key: "pair",
      direction: "asc",
    },
  );
  const chartId = useId();
  const joined =
    value.analysis_version === "bidirectional_block_cv_stationary_bootstrap_v1";
  const userLabel =
    value.user_portfolio_mode === "controlled"
      ? "Ваш портфель под управлением"
      : "Ваш портфель";
  const allocation = (weights: Record<string, number> | null | undefined) =>
    visibleWeights(weights)
      .map(
        ([asset, weight]) => `${ticker(asset, instruments)} ${percent(weight)}`,
      )
      .join(" · ") || "—";
  const hasPaths = value.results.some(
    (row) => row.wealth_log_f32?.user || row.wealth_log_f32?.optimized,
  );
  const rows = useMemo(() => {
    const keyFor = (
      row: PairedComparison["results"][number],
      index: number,
    ) => {
      if (sort.key === "pair") return row.pair ?? index + 1;
      if (sort.key === "weights") {
        if (!row.weights) return null;
        const entries = visibleWeights(row.weights)
          .map(
            ([asset, weight]) =>
              `${ticker(asset, instruments)} ${percent(weight)}`,
          )
          .sort((a, b) => a.localeCompare(b, "ru", { numeric: true }));
        return entries.length ? entries.join(" · ") : null;
      }
      const user = row.user?.[sort.key],
        optimized = row.optimized?.[sort.key];
      return typeof user === "number" &&
        Number.isFinite(user) &&
        typeof optimized === "number" &&
        Number.isFinite(optimized)
        ? optimized - user
        : null;
    };
    return value.results
      .map((row, index) => ({ row, index, key: keyFor(row, index) }))
      .sort((a, b) => {
        if (a.key == null) return b.key == null ? a.index - b.index : 1;
        if (b.key == null) return -1;
        const order =
          typeof a.key === "string" && typeof b.key === "string"
            ? a.key.localeCompare(b.key, "ru", { numeric: true })
            : Number(a.key) - Number(b.key);
        return (sort.direction === "asc" ? order : -order) || a.index - b.index;
      });
  }, [value.results, instruments, sort]);
  const header = (key: SortKey, label: string, title: string) => (
    <th
      scope="col"
      key={key}
      aria-sort={
        sort.key === key
          ? sort.direction === "asc"
            ? "ascending"
            : "descending"
          : "none"
      }
    >
      <button
        type="button"
        className="table-sort"
        title={title}
        onClick={() =>
          setSort((current) => ({
            key,
            direction:
              current.key === key && current.direction === "desc"
                ? "asc"
                : "desc",
          }))
        }
      >
        {label}{" "}
        <span aria-hidden="true">
          {sort.key === key ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}
        </span>
      </button>
    </th>
  );
  return (
    <details
      className="subsection"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Все выборки Validation ({value.results.length})</summary>
      {open && (
        <>
          <p className="hint">
            {userLabel} / оптимизированный портфель. Веса оптимизированного
            портфеля определены на Train. График показывает капитал в порядке
            месяцев Bootstrap. Сортировка показателей — по разнице оптимизатор −
            ваш портфель.
          </p>
          {!hasPaths && (
            <p className="hint">
              В сохранённом расчёте нет кривых капитала. Они появятся после
              нового запуска Bootstrap.
            </p>
          )}
          <div className="table-scroll">
            <table aria-label="Все выборки Validation">
              <thead>
                <tr>
                  {header(
                    "pair",
                    "Validation",
                    "Сортировать по номеру выборки",
                  )}
                  {joined ? (
                    <th scope="col">Целевые веса по Fold</th>
                  ) : (
                    header(
                      "weights",
                      "Веса",
                      "Сортировать по составу оптимизированного портфеля",
                    )
                  )}
                  {columns.map(([label, key]) =>
                    header(
                      key,
                      label,
                      "Сортировать по разнице: оптимизатор − ваш портфель",
                    ),
                  )}
                  <th scope="col">График</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ row, index }) => {
                  const pair = row.pair ?? index + 1;
                  const paths = row.wealth_log_f32;
                  const chartVisible =
                    selected === pair && !!(paths?.user || paths?.optimized);
                  const reason =
                    typeof row.diagnostics?.reason === "string"
                      ? row.diagnostics.reason
                      : row.status;
                  return (
                    <Fragment key={pair}>
                      <tr>
                        <th scope="row">
                          {pair}
                          {!row.optimized && (
                            <small className="hint" title={reason}>
                              <br />
                              {row.status}
                            </small>
                          )}
                        </th>
                        <td>
                          {joined ? (
                            row.fold_weights?.map((weights, i) => (
                              <div key={i}>
                                Fold {i + 1} · {allocation(weights)}
                              </div>
                            ))
                          ) : (
                            <>Ваш Портфель / {allocation(row.weights)}</>
                          )}
                        </td>
                        {columns.map(([label, key, format]) => (
                          <td key={label}>
                            {format(row.user?.[key])} /{" "}
                            {format(row.optimized?.[key])}
                          </td>
                        ))}
                        <td>
                          <button
                            type="button"
                            className="secondary"
                            disabled={!(paths?.user || paths?.optimized)}
                            aria-label={`График капитала Validation №${pair}`}
                            aria-expanded={chartVisible}
                            aria-controls={chartVisible ? chartId : undefined}
                            title={
                              paths?.user || paths?.optimized
                                ? "Кривые капитала"
                                : "Кривые капитала недоступны"
                            }
                            onClick={() =>
                              setSelected(chartVisible ? null : pair)
                            }
                          >
                            <span aria-hidden="true">↗</span>
                          </button>
                        </td>
                      </tr>
                      {chartVisible && (
                        <tr>
                          <td colSpan={7} id={chartId}>
                            <ValidationChart
                              pair={pair}
                              paths={paths!}
                              userLabel={userLabel}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </details>
  );
}
