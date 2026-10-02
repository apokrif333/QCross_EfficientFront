import type { Instrument, Portfolio } from "../types";
import { number, percent, ticker } from "../lib/display";
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
            <th>Доходность</th>
            <th>Волатильность</th>
            <th>Sharpe</th>
            <th>CAGR</th>
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
              <td>{percent(p.metrics?.volatility)}</td>
              <td>{number(p.metrics?.sharpe_ratio)}</td>
              <td>{percent(p.metrics?.historical_cagr)}</td>
              <td>{percent(p.metrics?.historical_max_drawdown)}</td>
              <td className="weight-cell">
                {p.weights ? (
                  Object.entries(p.weights).map(([a, w]) => (
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
