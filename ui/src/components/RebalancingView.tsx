import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import type {
  Instrument,
  RebalancingResult,
  RebalancingStrategy,
} from "../types";
import { number, percent, ticker, escapeHtml } from "../lib/display";
import Plot from "./Plot";

const columns = [
  ["annualized_cagr", "CAGR"],
  ["annualized_arithmetic_return", "Арифм. доходность"],
  ["annualized_volatility", "Волатильность"],
  ["historical_sharpe_ratio", "Sharpe"],
  ["sortino_ratio", "Sortino"],
  ["maximum_drawdown", "Макс. просадка"],
  ["terminal_wealth", "Капитал из $1"],
  ["total_commissions", "Накопленные комиссии, % от начального капитала"],
  ["trade_count", "Сделки"],
  ["rebalancing_events", "Ребалансировки"],
  ["annualized_turnover", "Оборот в год"],
  ["maximum_weight_deviation", "Макс. отклонение, п.п."],
] as const;
type SortKey = (typeof columns)[number][0];

function shortStrategy(s: RebalancingStrategy): string {
  if (s.threshold_percent != null) return `${s.threshold_percent}%`;
  const labels: Record<string, string> = {
    calendar_1: "месяц",
    calendar_3: "квартал",
    calendar_12: "год",
    calendar_24: "2 года",
    calendar_36: "3 года",
    calendar_48: "4 года",
    calendar_60: "5 лет",
    calendar_None: "B&H",
  };
  return labels[s.id] ?? s.label;
}

const byName = (a: RebalancingStrategy, b: RebalancingStrategy) =>
  a.label.localeCompare(b.label, "ru", { numeric: true });

export default function RebalancingView({
  result: r,
  instruments,
}: {
  result: RebalancingResult;
  instruments: Instrument[];
}) {
  const [gross, setGross] = useState(false),
    [selected, setSelected] = useState("calendar_12");
  const [sort, setSort] = useState<SortKey>("annualized_cagr"),
    [descending, setDescending] = useState(true);
  const side = gross ? "gross" : "net";
  const value = (s: RebalancingStrategy, key: SortKey): number | null =>
    key in s[side]
      ? s[side][key as keyof typeof s.net]
      : (s[key as keyof RebalancingStrategy] as number);
  const format = (s: RebalancingStrategy, key: SortKey) => {
    const v = value(s, key);
    if (key === "terminal_wealth") return v == null ? "—" : `$${number(v, 4)}`;
    if (key === "total_commissions") return percent(v, 4);
    if (
      [
        "historical_sharpe_ratio",
        "sortino_ratio",
        "trade_count",
        "rebalancing_events",
      ].includes(key)
    )
      return number(
        v,
        key.endsWith("count") || key === "rebalancing_events" ? 0 : 3,
      );
    return key === "maximum_weight_deviation"
      ? `${number(v == null ? null : v * 100, 2)} п.п.`
      : percent(v);
  };
  const rows = [...r.strategies].sort((a, b) => {
    const x = value(a, sort),
      y = value(b, sort);
    return x == null
      ? y == null
        ? 0
        : 1
      : y == null
        ? -1
        : (descending ? -1 : 1) * (x - y);
  });
  const active = r.strategies.find((s) => s.id === selected) ?? r.strategies[0];
  const plotData = useMemo(() => {
    const hover = (s: RebalancingStrategy) =>
      `<b>${escapeHtml(s.label)}</b><br>CAGR: ${percent(s[side].annualized_cagr)}<br>Волатильность: ${percent(s[side].annualized_volatility)}<br>Sharpe: ${number(s[side].historical_sharpe_ratio)}<br>Просадка: ${percent(s[side].maximum_drawdown)}<extra></extra>`;
    const calendar = [...r.strategies].sort(byName);
    const points = calendar.map((s) => ({
      x: s[side].annualized_volatility,
      y: s[side].annualized_cagr,
    }));
    const axes = {
      yaxis: { title: { text: "CAGR, % годовых" }, tickformat: ".1%" },
      xaxis: {
        title: { text: "Историческая волатильность" },
        tickformat: ".1%",
      },
    };
    return {
      calendar: calendar.map(
        (s, i) =>
          ({
            type: "scatter",
            mode: "text+markers",
            name: s.label,
            legendgroup: s.id,
            x: [points[i].x],
            y: [points[i].y],
            text: [shortStrategy(s)],
            textposition: "top center",
            hovertemplate: hover(s),
            showlegend: true,
            marker: {
              size: 12,
              color: s.annual_baseline ? "#b98943" : "#287f8e",
            },
          }) as Data,
      ),
      axes,
    };
  }, [r, side]);
  const dates = [
    `${r.months[0]}-01`,
    `${r.months[0]}-01`,
    ...r.months.map((m) => {
      const [y, month] = m.split("-").map(Number);
      return new Date(Date.UTC(y, month, 0)).toISOString().slice(0, 10);
    }),
  ];
  return (
    <>
      <p className="hint">
        Историческое сравнение на выбранной выборке. Первое место по CAGR не
        означает универсально лучшую стратегию. Начальные веса одинаковы; полная
        ребалансировка выполняется после наблюдения доходности месяца. Комиссия:{" "}
        {percent(r.transaction_fee_rate)} от каждой покупки и продажи, включая
        вход.
        {r.return_basis === "real" &&
          " В Real капитал и комиссии выражены в покупательной способности начала периода."}
      </p>
      <div className="segmented">
        <button
          aria-pressed={!gross}
          className={!gross ? "active" : ""}
          onClick={() => setGross(false)}
        >
          После комиссий
        </button>
        <button
          aria-pressed={gross}
          className={gross ? "active" : ""}
          onClick={() => setGross(true)}
        >
          До комиссий
        </button>
      </div>
      <div className="table-scroll rebalancing-table">
        <table aria-label="Сравнение правил ребалансировки">
          <thead>
            <tr>
              <th>Стратегия</th>
              {columns.map(([key, label]) => (
                <th
                  key={key}
                  aria-sort={
                    sort === key
                      ? descending
                        ? "descending"
                        : "ascending"
                      : "none"
                  }
                >
                  <button
                    className="secondary"
                    onClick={() => {
                      setSort(key);
                      setDescending(sort === key ? !descending : true);
                    }}
                  >
                    {label}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr
                key={s.id}
                className={s.annual_baseline ? "annual-baseline" : ""}
              >
                <td>
                  <button
                    className="secondary"
                    onClick={() => setSelected(s.id)}
                  >
                    {s.label}
                    {s.annual_baseline ? " · базовый" : ""}
                  </button>
                </td>
                {columns.map(([k]) => (
                  <td key={k}>{format(s, k)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">
        Комиссии и число сделок относятся к фактическому исполнению после
        затрат. Оборот — сумма покупок и продаж / капитал до сделки, в год, без
        первоначального входа.
      </p>
      <h4>Правила ребалансировки: фактические доходность и риск</h4>
      <Plot
        title="Правила ребалансировки: фактические доходность и риск"
        data={plotData.calendar}
        layout={plotData.axes}
      />
      <h3>Кривые капитала {gross ? "до" : "после"} комиссий</h3>
      <Plot
        title="Капитал по правилам ребалансировки"
        data={[...r.strategies].sort(byName).map(
          (s) =>
            ({
              type: "scatter",
              mode: "lines",
              name: s.label,
              x: dates,
              y: gross
                ? [1, ...s.gross_wealth]
                : [
                    1,
                    s.initial_net_wealth ?? 1 - (s.events[0]?.commissions ?? 0),
                    ...s.net_wealth.slice(1),
                  ],
              visible:
                s.annual_baseline ||
                s.id === selected ||
                s.id === "calendar_1" ||
                s.id === "calendar_None"
                  ? true
                  : "legendonly",
              line: { width: s.annual_baseline ? 3 : 1.5 },
              hovertemplate: `${escapeHtml(s.label)}<br>%{x}<br>Капитал из $1: %{y:.4f}<extra></extra>`,
            }) as Data,
        )}
        layout={{
          yaxis: { title: { text: "Капитал из $1" } },
          xaxis: { title: { text: "Месяц" } },
        }}
      />
      <label>
        Стратегия для весов и сделок
        <select value={active.id} onChange={(e) => setSelected(e.target.value)}>
          {[...r.strategies].sort(byName).map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      <h3>Фактические веса после исполнения на конец месяца</h3>
      <Plot
        title="Фактические веса портфеля"
        data={r.assets
          .map((a, i) => ({ a, i }))
          .filter(({ i }) =>
            active.weight_history.some((w) => Math.abs(w[i]) > 1e-12),
          )
          .map(
            ({ a, i }) =>
              ({
                type: "scatter",
                mode: "lines",
                name: ticker(a, instruments),
                x: r.months,
                y: active.weight_history.map((w) => w[i]),
                customdata: active.weight_history.map((w) => w[i]),
                stackgroup: "weights",
                hovertemplate: `${escapeHtml(ticker(a, instruments))}<br>%{x}: %{customdata:.2%}<extra></extra>`,
              }) as Data,
          )}
        layout={{
          yaxis: { tickformat: ".0%", range: [0, 1] },
          shapes: [
            ...active.events
              .filter((e) => e.kind === "rebalance")
              .map((e) => ({
                type: "line" as const,
                xref: "x" as const,
                yref: "paper" as const,
                x0: e.date,
                x1: e.date,
                y0: 0,
                y1: 1,
                line: {
                  color: "rgba(50,50,50,0.4)",
                  width: 1,
                  dash: "dot" as const,
                },
              })),
          ],
        }}
      />
      <details>
        <summary>Сделки и события: {active.label}</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Месяц / момент</th>
                <th>Триггеры</th>
                <th>Веса перед ребалансировкой</th>
                <th>Сделки, $</th>
                <th>Оборот</th>
                <th title="Доля капитала непосредственно перед сделкой">
                  Комиссии, % капитала
                </th>
              </tr>
            </thead>
            <tbody>
              {active.events.map((e, i) => (
                <tr key={i}>
                  <td>
                    {e.date} ·{" "}
                    {e.kind === "initial_purchase"
                      ? "начальная покупка"
                      : "конец месяца"}
                  </td>
                  <td>
                    {e.triggering_assets
                      .map((a) => ticker(a, instruments))
                      .join(", ") || "календарь / вход"}
                  </td>
                  <td>
                    {e.kind === "initial_purchase"
                      ? "—"
                      : r.assets
                          .map((a, index) => ({
                            a,
                            weight:
                              active.pretrade_weight_history[e.month_index]?.[
                                index
                              ] ?? 0,
                          }))
                          .filter(({ weight }) => weight > 1e-12)
                          .map(({ a, weight }) => (
                            <small key={a}>
                              {ticker(a, instruments)} {percent(weight)}
                            </small>
                          ))}
                  </td>
                  <td>
                    {Object.entries(e.trades)
                      .filter(([, v]) => Math.abs(v) > 1e-12)
                      .map(([a, v]) => (
                        <small key={a}>
                          {ticker(a, instruments)}{" "}
                          {v > 0 ? "покупка" : "продажа"} $
                          {number(Math.abs(v), 5)}
                        </small>
                      ))}
                  </td>
                  <td>{percent(e.turnover)}</td>
                  <td>{percent(e.turnover * r.transaction_fee_rate, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
