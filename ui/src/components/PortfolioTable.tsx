import type { Instrument, Portfolio } from "../types";
import { annualCostKeys, costLabels } from "../lib/tradingCosts";
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
  historicalSharpeOnly = false,
  showAnnualCosts = true,
}: {
  rows: { label: string; portfolio: Portfolio }[];
  instruments: Instrument[];
  onSelect?: (p: Portfolio, label: string) => void;
  historicalSharpeOnly?: boolean;
  showAnnualCosts?: boolean;
}) {
  const visibleCosts = annualCostKeys.filter(
    (key) =>
      showAnnualCosts &&
      rows.some(
        ({ portfolio }) => portfolio.metrics?.trading_costs?.[key] != null,
      ),
  );
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
            {!historicalSharpeOnly && <th>Estimated Sharpe</th>}
            <th>{historicalSharpeOnly ? "Sharpe" : "Historical Sharpe"}</th>
            <th>Историческая CAGR</th>
            <th>Просадка</th>
            {visibleCosts.map((key) => (
              <th key={key}>{costLabels[key]} Net</th>
            ))}
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
                {p.metrics?.tax_insolvent && (
                  <small>
                    Непогашенный налог на $1: $
                    {number(
                      p.metrics.trading_costs?.unpaid_capital_gains_tax,
                      4,
                    )}
                  </small>
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
              {!historicalSharpeOnly && (
                <td>{number(p.metrics?.sharpe_ratio)}</td>
              )}
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
              {visibleCosts.map((key) => (
                <td key={key}>{percent(p.metrics?.trading_costs?.[key], 4)}</td>
              ))}
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
