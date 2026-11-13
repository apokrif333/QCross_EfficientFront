import type { Instrument, Portfolio } from "../types";
import {
  displayedVolatility,
  number,
  percent,
  ticker,
  visibleWeights,
} from "../lib/display";
export default function PortfolioTable({
  rows,
  instruments,
  onSelect,
}: {
  rows: { label: string; portfolio: Portfolio }[];
  instruments: Instrument[];
  onSelect?: (p: Portfolio, label: string) => void;
}) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Портфель</th>
            <th>
              {rows.some(
                ({ portfolio }) => portfolio.status === "historical_path",
              )
                ? "Годовая доходность (арифм.)"
                : "Ожидаемая (арифм.)"}
            </th>
            <th>Волатильность</th>
            <th>Estimated Sharpe</th>
            <th>Historical Sharpe</th>
            <th>Историческая CAGR</th>
            <th>Просадка</th>
            <th>Веса</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ label, portfolio: p }) => (
            <tr key={label}>
              <td>
                <button
                  className="text-button"
                  onClick={() => onSelect?.(p, label)}
                >
                  {label}
                </button>
                {p.feasible === false && (
                  <small>Вне ограничений · reference</small>
                )}
              </td>
              <td>
                {percent(
                  p.metrics?.expected_return,
                  p.status === "historical_path" ? 3 : 2,
                )}
              </td>
              <td>
                {percent(
                  displayedVolatility(p.metrics),
                  p.status === "historical_path" ? 3 : 2,
                )}
              </td>
              <td>{number(p.metrics?.sharpe_ratio)}</td>
              <td>
                {number(
                  p.metrics?.historical_sharpe_ratio,
                  p.status === "historical_path" ? 4 : 3,
                )}
              </td>
              <td>
                {percent(
                  p.metrics?.historical_cagr,
                  p.status === "historical_path" ? 3 : 2,
                )}
              </td>
              <td>
                {percent(
                  p.metrics?.historical_max_drawdown,
                  p.status === "historical_path" ? 3 : 2,
                )}
              </td>
              <td className="weight-cell">
                {p.weights ? (
                  visibleWeights(p.weights, 1).map(([a, w]) => (
                    <span key={a}>
                      {ticker(a, instruments)} <b>{percent(w, 1)}</b>
                    </span>
                  ))
                ) : (
                  <span>
                    {p.status === "historical_path"
                      ? "Динамические веса"
                      : p.status}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
