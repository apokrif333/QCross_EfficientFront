import { useMemo, useState } from "react";
import { number, percent } from "../lib/display";
import { linearQuantile, trendHeatmap } from "../lib/statistics";
import { annualCostHint } from "../lib/tradingCosts";
import type { BootstrapTrendSeries } from "../types";

const HEATMAP_METRICS = [
  ["medianCagr", "CAGR"],
  ["medianVolatility", "Vol"],
  ["medianDrawdown", "DD"],
  ["medianSharpe", "Sharpe"],
  ["medianSortino", "Sortino"],
  ["calmar", "Calmar"],
] as const;
const METRICS = [
  ...HEATMAP_METRICS,
  ["p5Cagr", "P5 CAGR"],
  ["p95Drawdown", "P95 макс. просадки"],
  ["commissions", "Среднегодовые комиссии"],
  ["tax", "Годовой налог на прирост капитала"],
  ["costs", "Совокупные годовые издержки"],
  ["higherCagr", "Выше CAGR эталона"],
  ["higherCagrLowerDrawdown", "Выше CAGR и ниже просадка"],
] as const;
const COLUMNS = [
  ["period", "Период"],
  ...METRICS,
  ["pareto", "Парето"],
] as const;
type Column = (typeof COLUMNS)[number][0];
const COSTS = ["commissions", "tax", "costs"] as const;

function quantile(sorted: number[], q: number) {
  return { value: linearQuantile(sorted, q), count: sorted.length };
}

function mean(total: number, count: number) {
  return { value: count ? total / count : null, count };
}

export function summarizeBootstrapTrend(
  series: BootstrapTrendSeries,
  benchmark: number,
  gross: boolean,
) {
  const offset = gross ? 3 : 0;
  const ratioOffset = gross ? 15 : 13;
  const reference = series[benchmark - 1];
  const rows = series.map((samples, i) => {
    const returns: number[] = [],
      drawdowns: number[] = [],
      volatilities: number[] = [],
      sharpes: number[] = [],
      sortinos: number[] = [],
      calmars: number[] = [];
    let fees = 0,
      feeCount = 0,
      tax = 0,
      taxCount = 0,
      costs = 0,
      costCount = 0,
      cagrWins = 0,
      cagrPairs = 0,
      jointWins = 0,
      jointPairs = 0;
    samples.forEach((point, draw) => {
      const cagr = point[offset],
        drawdown = point[offset + 1],
        commission = point[6];
      if (cagr != null) returns.push(cagr);
      if (drawdown != null) drawdowns.push(drawdown);
      const volatility = point[offset + 2];
      const sharpe = point[ratioOffset];
      const sortino = point[ratioOffset + 1];
      if (volatility != null) volatilities.push(volatility);
      if (sharpe != null) sharpes.push(sharpe);
      if (sortino != null) sortinos.push(sortino);
      if (cagr != null && drawdown != null && drawdown > 0) {
        const calmar = cagr / drawdown;
        if (Number.isFinite(calmar)) calmars.push(calmar);
      }
      if (commission != null) {
        fees += commission;
        feeCount++;
      }
      if (point[7] != null) {
        tax += point[7];
        taxCount++;
      }
      if (point[8] != null) {
        costs += point[8];
        costCount++;
      }
      const refCagr = reference[draw]?.[offset],
        refDrawdown = reference[draw]?.[offset + 1];
      if (cagr != null && refCagr != null) {
        const wins = cagr > refCagr;
        cagrPairs++;
        cagrWins += Number(wins);
        if (drawdown != null && refDrawdown != null) {
          jointPairs++;
          jointWins += Number(wins && drawdown < refDrawdown);
        }
      }
    });
    returns.sort((a, b) => a - b);
    drawdowns.sort((a, b) => a - b);
    volatilities.sort((a, b) => a - b);
    sharpes.sort((a, b) => a - b);
    sortinos.sort((a, b) => a - b);
    calmars.sort((a, b) => a - b);
    return {
      period: i + 1,
      medianCagr: quantile(returns, 0.5),
      p5Cagr: quantile(returns, 0.05),
      medianDrawdown: quantile(drawdowns, 0.5),
      medianVolatility: quantile(volatilities, 0.5),
      medianSharpe: quantile(sharpes, 0.5),
      medianSortino: quantile(sortinos, 0.5),
      p95Drawdown: quantile(drawdowns, 0.95),
      calmar: quantile(calmars, 0.5),
      commissions: mean(fees, feeCount),
      tax: mean(tax, taxCount),
      costs: mean(costs, costCount),
      higherCagr: mean(cagrWins, cagrPairs),
      higherCagrLowerDrawdown: mean(jointWins, jointPairs),
    };
  });
  return rows.map((row) => {
    const cagr = row.medianCagr.value,
      risk = row.p95Drawdown.value;
    const pareto =
      cagr == null || risk == null
        ? null
        : !rows.some((other) => {
            const otherCagr = other.medianCagr.value,
              otherRisk = other.p95Drawdown.value;
            return (
              otherCagr != null &&
              otherRisk != null &&
              otherCagr >= cagr &&
              otherRisk <= risk &&
              (otherCagr > cagr || otherRisk < risk)
            );
          });
    return { ...row, pareto };
  });
}

type Row = ReturnType<typeof summarizeBootstrapTrend>[number];
function sortValue(row: Row, column: Column) {
  if (column === "period") return row.period;
  if (column === "pareto")
    return row.pareto == null ? null : Number(row.pareto);
  return row[column].value;
}

export default function BootstrapTrendComparison({
  series,
  benchmark,
  gross,
  iterations,
  commissionBasis,
  onSelect,
}: {
  series: BootstrapTrendSeries;
  benchmark: number;
  gross: boolean;
  iterations: number;
  commissionBasis?: "pretrade_nav";
  onSelect: (period: number) => void;
}) {
  const [sort, setSort] = useState<{ column: Column; descending: boolean }>({
    column: "period",
    descending: false,
  });
  const summary = useMemo(
    () => summarizeBootstrapTrend(series, benchmark, gross),
    [series, benchmark, gross],
  );
  const legacyCommissions =
    !commissionBasis && summary.some((row) => row.commissions.count > 0);
  const hidden = (column: Column) =>
    COSTS.some((key) => key === column) &&
    (gross ||
      !summary.some((row) =>
        COSTS.some((key) => key === column && row[key].count > 0),
      ));
  const rows = [...summary].sort((a, b) => {
    const x = sortValue(a, sort.column),
      y = sortValue(b, sort.column);
    if (x == null || y == null)
      return Number(x == null) - Number(y == null) || a.period - b.period;
    return (sort.descending ? y - x : x - y) || a.period - b.period;
  });
  const colors = new Map<Column, (string | undefined)[]>(
    HEATMAP_METRICS.map(
      ([metric]) =>
        [
          metric,
          trendHeatmap(
            rows.map((row) => row[metric].value),
            metric === "medianVolatility" || metric === "medianDrawdown",
          ),
        ] as const,
    ),
  );
  return (
    <>
      <p className="hint" aria-live="polite">
        Эталон — {benchmark} мес. Нажмите на строку, чтобы его сменить.
      </p>
      <div className="table-scroll bootstrap-trend-table-scroll">
        <table
          className="bootstrap-trend-table"
          aria-label="Сравнение периодов SMA/Momentum"
        >
          <thead>
            <tr>
              {COLUMNS.filter(([column]) => !hidden(column)).map(
                ([column, label]) => (
                  <th
                    key={column}
                    scope="col"
                    aria-sort={
                      sort.column === column
                        ? sort.descending
                          ? "descending"
                          : "ascending"
                        : undefined
                    }
                  >
                    <button
                      className="table-sort"
                      type="button"
                      onClick={() =>
                        setSort((current) => ({
                          column,
                          descending:
                            current.column === column
                              ? !current.descending
                              : column !== "period",
                        }))
                      }
                    >
                      {column === "commissions" && legacyCommissions
                        ? "Комиссии за год / начальный капитал"
                        : label}
                      {sort.column === column && (
                        <span aria-hidden="true">
                          {" "}
                          {sort.descending ? "↓" : "↑"}
                        </span>
                      )}
                    </button>
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={row.period}
                data-benchmark={row.period === benchmark}
                onClick={() => onSelect(row.period)}
              >
                <th scope="row">
                  <button
                    className="table-sort"
                    type="button"
                    aria-pressed={row.period === benchmark}
                  >
                    {row.period} мес.
                  </button>
                </th>
                {METRICS.filter(([metric]) => !hidden(metric)).map(
                  ([metric]) => {
                    const { value, count } = row[metric];
                    return (
                      <td
                        key={metric}
                        style={{ backgroundColor: colors.get(metric)?.[i] }}
                      >
                        {metric === "calmar" ||
                        metric === "medianSharpe" ||
                        metric === "medianSortino"
                          ? number(value)
                          : percent(value, metric === "commissions" ? 4 : 2)}
                        <small>
                          {count} / {iterations}
                        </small>
                      </td>
                    );
                  },
                )}
                <td>
                  {row.pareto == null ? (
                    "—"
                  ) : row.pareto ? (
                    <strong>Да</strong>
                  ) : (
                    "Нет"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">
        CAGR, Vol, DD, Sharpe, Sortino и Calmar показаны как медианы по
        генерациям. Под значением указано число доступных генераций или пар.
        {legacyCommissions &&
          " В этом сохранённом расчёте комиссии за весь период поделены на начальный капитал и число лет."}
        {commissionBasis === "pretrade_nav" && !gross && ` ${annualCostHint}`}
      </p>
    </>
  );
}
