import { useState } from "react";
import Plot from "./Plot";
import { number, percent } from "../lib/display";

export default function ControlResults({ result }: { result: any }) {
  const [gross, setGross] = useState(false);
  const c = result.portfolio_controls;
  if (!c) return null;
  const side = gross ? "gross" : "net";
  return (
    <section className="subsection">
      <h3>Управление портфелем: исходный / управляемый</h3>
      <button onClick={() => setGross(!gross)}>
        {gross ? "До комиссий" : "После комиссий"}
      </button>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Метрика</th>
              <th>Исходный</th>
              <th>Управляемый</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(c.baseline[side]).map((k) => (
              <tr key={k}>
                <td>{k}</td>
                <td>{number(c.baseline[side][k], 4)}</td>
                <td>{number(c.controlled[side][k], 4)}</td>
              </tr>
            ))}
            {[
              "total_commissions",
              "trade_count",
              "rebalancing_events",
              "annualized_turnover",
            ].map((k) => (
              <tr key={k}>
                <td>{k}</td>
                <td>{number(c.baseline[k], 4)}</td>
                <td>{number(c.controlled[k], 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Plot
        title="Капитал: исходный и управляемый"
        data={["baseline", "controlled"].map((k) => ({
          type: "scatter",
          mode: "lines",
          name: k === "baseline" ? "Исходный" : "Управляемый",
          x: [c.months[0], ...c.months],
          y: c[k][`${side}_wealth`],
        }))}
      />
      <Plot
        title="Управляемый портфель: фактические веса"
        data={c.assets.map((a: string, i: number) => ({
          type: "scatter",
          mode: "lines",
          stackgroup: "weights",
          name: a,
          x: c.months,
          y: c.controlled.weight_history.map((w: number[]) => w[i]),
          customdata: c.controlled.weight_history.map((w: number[]) => w[i]),
          hovertemplate: "%{x}: %{customdata:.2%}<extra></extra>",
        }))}
        layout={{ yaxis: { tickformat: ".0%" } }}
      />
      <Plot
        title="Downside volatility и экспозиция"
        data={["alpha", "downside_volatility", "hedge_allocation"].map((k) => ({
          type: "scatter",
          mode: "lines",
          name: k,
          x: c.months,
          y: c.controlled.signal_history.map((s: any) => s[k]),
        }))}
      />
      <details>
        <summary>Сигналы и сделки</summary>
        <table>
          <tbody>
            {c.controlled.events.map((e: any, i: number) => (
              <tr key={i}>
                <td>
                  {e.month_index < 0
                    ? "Начальная покупка"
                    : c.months[e.month_index]}
                </td>
                <td>{e.reason ?? e.kind}</td>
                <td>{percent(e.commissions)}</td>
                <td>{JSON.stringify(e.trades)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      {result.folds && (
        <details>
          <summary>CV: управляемый портфель на Validation</summary>
          <pre>
            {JSON.stringify(
              result.folds.map((f: any) => ({
                fold: f.fold,
                validation: f.validation,
                baseline: f.portfolio_controls?.baseline.net,
                controlled: f.portfolio_controls?.controlled.net,
              })),
              null,
              2,
            )}
          </pre>
        </details>
      )}
      {result.portfolio_control_samples?.length > 0 && (
        <details>
          <summary>
            Bootstrap: {result.portfolio_control_samples.length} сценариев,
            управляемые результаты
          </summary>
          <pre>
            {JSON.stringify(
              result.portfolio_control_samples.map((s: any) => ({
                baseline: s.baseline.net,
                controlled: s.controlled.net,
              })),
              null,
              2,
            )}
          </pre>
        </details>
      )}
    </section>
  );
}
