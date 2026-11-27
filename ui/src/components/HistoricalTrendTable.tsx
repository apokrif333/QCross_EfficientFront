import { number, percent } from "../lib/display";
import { trendHeatmap } from "../lib/statistics";
import type { TrendSensitivityPoint } from "../types";

const COLUMNS = [
  { key: "annualized_cagr", label: "CAGR", format: percent },
  {
    key: "annualized_volatility",
    label: "Vol",
    format: percent,
    lowerIsBetter: true,
  },
  {
    key: "maximum_drawdown",
    label: "DD",
    format: percent,
    lowerIsBetter: true,
  },
  { key: "historical_sharpe_ratio", label: "Sharpe", format: number },
  { key: "sortino_ratio", label: "Sortino", format: number },
  { key: "calmar_ratio", label: "Calmar", format: number },
] as const;

export default function HistoricalTrendTable({
  points,
  side,
}: {
  points: TrendSensitivityPoint[];
  side: "gross" | "net";
}) {
  const rows = [...points].sort(
    (a, b) => a.lookback_months - b.lookback_months,
  );
  const columns = COLUMNS.map((column) => ({
    ...column,
    colors: trendHeatmap(
      rows.map((point) => point[side][column.key]),
      "lowerIsBetter" in column && column.lowerIsBetter,
    ),
  }));
  return (
    <div className="table-scroll trend-table-scroll">
      <table
        className="trend-table"
        aria-label="Показатели по периодам SMA/Momentum"
      >
        <thead>
          <tr>
            <th scope="col" aria-sort="ascending">
              Период
            </th>
            {columns.map(({ key, label }) => (
              <th key={key} scope="col">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((point, i) => (
            <tr key={point.lookback_months}>
              <th scope="row">{point.lookback_months} мес.</th>
              {columns.map(({ key, format, colors }) => (
                <td key={key} style={{ backgroundColor: colors[i] }}>
                  {format(point[side][key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
