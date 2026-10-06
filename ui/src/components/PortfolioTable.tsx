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
            <th>Ожидаемая (арифм.)</th>
            <th>Волатильность</th>
            <th>Sharpe</th>
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
              <td>{percent(p.metrics?.expected_return)}</td>
              <td>{percent(displayedVolatility(p.metrics))}</td>
              <td>{number(p.metrics?.sharpe_ratio)}</td>
              <td>{percent(p.metrics?.historical_cagr)}</td>
              <td>{percent(p.metrics?.historical_max_drawdown)}</td>
              <td className="weight-cell">
                {p.weights ? (
                  visibleWeights(p.weights, 1).map(([a, w]) => (
                    <span key={a}>
                      {ticker(a, instruments)} <b>{percent(w, 1)}</b>
                    </span>
                  ))
                ) : (
                  <span>{p.status}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
