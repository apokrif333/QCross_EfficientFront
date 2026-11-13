import { useState } from "react";
import Plot from "./Plot";
import { number, percent, ticker } from "../lib/display";
import type { Instrument } from "../types";

const labels: Record<string, string> = {
  annualized_cagr: "CAGR",
  annualized_arithmetic_return: "Годовая арифметическая доходность",
  annualized_volatility: "Историческая годовая волатильность",
  historical_sharpe_ratio: "Historical Sharpe",
  sortino_ratio: "Sortino",
  maximum_drawdown: "Максимальная просадка",
  terminal_wealth: "Капитал из $1",
  total_commissions: "Накопленные комиссии, % начального капитала",
  trade_count: "Количество сделок",
  rebalancing_events: "Ребалансировки",
  annualized_turnover: "Оборот в год",
};
const metric = (key: string, v: number | null) =>
  [
    "historical_sharpe_ratio",
    "sortino_ratio",
    "trade_count",
    "rebalancing_events",
  ].includes(key)
    ? number(v, key.includes("count") || key === "rebalancing_events" ? 0 : 3)
    : key === "terminal_wealth"
      ? `$${number(v, 4)}`
      : percent(v, 3);

export default function ControlResults({
  result,
  instruments,
}: {
  result: any;
  instruments: Instrument[];
}) {
  const [gross, setGross] = useState(false);
  const c = result.portfolio_controls;
  if (!c) return null;
  const side = gross ? "gross" : "net";
  const name = (a: string) =>
    a === "BIL_REFERENCE" ? "BIL" : ticker(a, instruments);
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
                <td>{labels[k] ?? k}</td>
                <td>{metric(k, c.baseline[side][k])}</td>
                <td>{metric(k, c.controlled[side][k])}</td>
              </tr>
            ))}
            {[
              "total_commissions",
              "trade_count",
              "rebalancing_events",
              "annualized_turnover",
            ].map((k) => (
              <tr key={k}>
                <td>{labels[k] ?? k}</td>
                <td>{metric(k, c.baseline[k])}</td>
                <td>{metric(k, c.controlled[k])}</td>
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
        data={c.assets
          .map((a: string, i: number) => ({ a, i }))
          .filter(({ i }: { i: number }) =>
            c.controlled.weight_history.some(
              (w: number[]) => Math.abs(w[i]) > 1e-12,
            ),
          )
          .map(({ a, i }: { a: string; i: number }) => ({
            type: "scatter",
            mode: "lines",
            stackgroup: "weights",
            name: name(a),
            x: c.months,
            y: c.controlled.weight_history.map((w: number[]) => w[i]),
            customdata: c.controlled.weight_history.map((w: number[]) => w[i]),
            hovertemplate: "%{x}: %{customdata:.2%}<extra></extra>",
          }))}
        layout={{ yaxis: { tickformat: ".0%" } }}
      />
      {c.config.mode === "downside_vol" && (
        <Plot
          title="Downside volatility и экспозиция"
          data={[
            "alpha",
            "downside_volatility",
            "target_downside_volatility",
          ].map((k) => ({
            type: "scatter",
            mode: "lines",
            name: k,
            x: c.months,
            y: c.controlled.signal_history.map((s: any) => s[k]),
          }))}
        />
      )}
      <Plot
        title="Целевая доля: исходный портфель и хедж"
        data={["alpha", "hedge_allocation"].map((k) => ({
          type: "scatter",
          mode: "lines",
          name: k === "alpha" ? "Исходный портфель" : "Хедж",
          x: c.months,
          y: c.controlled.signal_history.map((s: any) => s[k]),
        }))}
        layout={{ yaxis: { tickformat: ".0%" } }}
      />
      {c.config.mode === "trend" && (
        <Plot
          title="SMA/Momentum: активные сигналы"
          data={Object.keys(c.config.trend.asset_rules).map((a) => ({
            type: "scatter",
            mode: "lines",
            name: name(a),
            x: c.months,
            y: c.controlled.signal_history.map((s: any) =>
              s.signals?.[a]?.active ? 1 : 0,
            ),
            line: { shape: "hv" },
          }))}
          layout={{
            yaxis: { tickvals: [0, 1], ticktext: ["Неактивен", "Активен"] },
          }}
        />
      )}
      {c.sensitivity && (
        <Plot
          title={`Чувствительность SMA/Momentum: ${name(c.sensitivity.asset)}`}
          threeD
          height={500}
          data={[
            {
              type: "scatter3d",
              mode: "lines+markers",
              x: c.sensitivity.points.map((p: any) => p[side].annualized_cagr),
              y: c.sensitivity.points.map(
                (p: any) => p[side].annualized_volatility,
              ),
              z: c.sensitivity.points.map((p: any) => p.lookback_months),
              text: c.sensitivity.points.map(
                (p: any) =>
                  `Период: ${p.lookback_months}<br>CAGR: ${percent(p[side].annualized_cagr)}<br>Vol: ${percent(p[side].annualized_volatility)}<br>Sharpe: ${number(p[side].historical_sharpe_ratio)}<br>Просадка: ${percent(p[side].maximum_drawdown)}<br>Комиссии: ${percent(p.total_commissions)} начального капитала`,
              ),
              hovertemplate: "%{text}<extra></extra>",
            },
          ]}
          layout={{
            scene: {
              xaxis: { title: { text: "CAGR" }, tickformat: ".1%" },
              yaxis: {
                title: { text: "Годовая волатильность" },
                tickformat: ".1%",
              },
              zaxis: { title: { text: "Период, месяцев" } },
            },
          }}
        />
      )}
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
                <td>
                  {e.reason === "control_change"
                    ? "Изменение управляющего сигнала"
                    : e.reason === "rebalancing_policy"
                      ? "Плановая ребалансировка"
                      : "Начальная покупка"}
                  <br />
                  {e.control_state?.entries?.length > 0 &&
                    `Вход: ${e.control_state.entries.map(name).join(", ")}`}
                  {e.control_state?.exits?.length > 0 &&
                    ` Выход: ${e.control_state.exits.map(name).join(", ")}`}
                </td>
                <td>
                  {percent(
                    e.turnover *
                      (result.simulation_settings?.transaction_fee_rate ?? 0),
                    4,
                  )}
                </td>
                <td>
                  {c.assets.map(
                    (a: string, index: number) =>
                      Math.abs(e.trades[index]) > 1e-12 && (
                        <small key={a}>
                          {name(a)}:{" "}
                          {e.trades[index] > 0 ? "покупка" : "продажа"} $
                          {number(Math.abs(e.trades[index]), 5)}
                        </small>
                      ),
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      {result.folds && (
        <details>
          <summary>CV: управляемый портфель на Validation</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Fold / Validation</th>
                  <th>Портфель</th>
                  {[
                    "annualized_cagr",
                    "annualized_volatility",
                    "historical_sharpe_ratio",
                    "maximum_drawdown",
                  ].map((k) => (
                    <th key={k}>{labels[k]}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.folds.flatMap((f: any) =>
                  ["baseline", "controlled"].map((k) => (
                    <tr key={`${f.fold}-${k}`}>
                      <td>
                        {f.fold}: {f.validation.start}—{f.validation.end}
                      </td>
                      <td>{k === "baseline" ? "Исходный" : "Управляемый"}</td>
                      {[
                        "annualized_cagr",
                        "annualized_volatility",
                        "historical_sharpe_ratio",
                        "maximum_drawdown",
                      ].map((m) => (
                        <td key={m}>
                          {metric(m, f.portfolio_controls?.[k]?.[side]?.[m])}
                        </td>
                      ))}
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {result.portfolio_control_samples?.length > 0 && (
        <details>
          <summary>
            Bootstrap: {result.portfolio_control_samples.length} сценариев,
            управляемые результаты
          </summary>
          <p className="hint">
            Полная длина истории: {result.period.observations} месяцев.
            Исторические синтетические сценарии, не вероятности будущей
            доходности. Ошибок управления:{" "}
            {
              result.portfolio_control_samples.filter(
                (s: any) => s.status === "failed",
              ).length
            }
            .
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Портфель / метрика</th>
                  <th>Mean</th>
                  <th>Median</th>
                  <th>P5</th>
                  <th>P95</th>
                  <th>Min</th>
                  <th>Max</th>
                  <th>N</th>
                </tr>
              </thead>
              <tbody>
                {["baseline", "controlled"].flatMap((k) =>
                  Object.keys(c.baseline[side]).map((m) => {
                    const values = result.portfolio_control_samples
                      .map((s: any) => s[k]?.[side]?.[m])
                      .filter(
                        (v: any) => typeof v === "number" && Number.isFinite(v),
                      )
                      .sort((a: number, b: number) => a - b);
                    const q = (p: number) => {
                      const pos = (values.length - 1) * p;
                      const lo = Math.floor(pos);
                      return values.length
                        ? values[lo] +
                            (values[Math.ceil(pos)] - values[lo]) * (pos - lo)
                        : null;
                    };
                    return (
                      <tr key={`${k}-${m}`}>
                        <td>
                          {k === "baseline" ? "Исходный" : "Управляемый"} ·{" "}
                          {labels[m]}
                        </td>
                        {[
                          values.length
                            ? values.reduce(
                                (a: number, b: number) => a + b,
                                0,
                              ) / values.length
                            : null,
                          q(0.5),
                          q(0.05),
                          q(0.95),
                          q(0),
                          q(1),
                        ].map((v, i) => (
                          <td key={i}>{metric(m, v)}</td>
                        ))}
                        <td>{values.length}</td>
                      </tr>
                    );
                  }),
                )}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
