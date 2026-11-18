import { useState } from "react";
import Plot from "./Plot";
import { number, percent, ticker, escapeHtml } from "../lib/display";
import type {
  AssetTrendSensitivityResult,
  Instrument,
  TrendSensitivityPoint,
} from "../types";

function sensitivityRegression(
  points: TrendSensitivityPoint[],
  side: "gross" | "net",
) {
  const pairs = points.flatMap((point) => {
    const x = point[side].maximum_drawdown;
    const y = point[side].annualized_cagr;
    return typeof x === "number" &&
      Number.isFinite(x) &&
      typeof y === "number" &&
      Number.isFinite(y)
      ? [{ x, y }]
      : [];
  });
  if (pairs.length < 2) return null;
  const x = [
    Math.min(...pairs.map((p) => p.x)),
    Math.max(...pairs.map((p) => p.x)),
  ];
  const yMin = Math.min(...pairs.map((p) => p.y));
  const yMax = Math.max(...pairs.map((p) => p.y));
  if (x[0] === x[1]) return null;
  const meanX = pairs.reduce((sum, p) => sum + p.x, 0) / pairs.length;
  const meanY = pairs.reduce((sum, p) => sum + p.y, 0) / pairs.length;
  const xx = pairs.reduce((sum, p) => sum + (p.x - meanX) ** 2, 0);
  const yy = pairs.reduce((sum, p) => sum + (p.y - meanY) ** 2, 0);
  if (xx === 0) return null;
  const xy = pairs.reduce((sum, p) => sum + (p.x - meanX) * (p.y - meanY), 0);
  const correlation =
    yMin === yMax || yy === 0
      ? null
      : Math.max(-1, Math.min(1, xy / Math.sqrt(xx * yy)));
  const y = x.map((value) => meanY + (xy / xx) * (value - meanX));
  return { correlation, x, y };
}

const labels: Record<string, string> = {
  annualized_cagr: "CAGR",
  annualized_arithmetic_return: "Годовая арифметическая доходность",
  annualized_volatility: "Историческая годовая волатильность",
  annualized_downside_volatility: "Нисходящая годовая волатильность",
  historical_sharpe_ratio: "Historical Sharpe",
  sortino_ratio: "Sortino",
  maximum_drawdown: "Максимальная просадка",
  terminal_wealth: "Капитал из $1",
  average_annual_commission: "Среднегодовые комиссии, % капитала",
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

function drawdowns(wealth: (number | null)[]): (number | null)[] {
  let peak = 0;
  let missing = false;
  return wealth.map((value) => {
    if (value == null || !Number.isFinite(value) || value < 0) missing = true;
    if (missing || value == null) return null;
    peak = Math.max(peak, value);
    return peak > 0 ? value / peak - 1 : null;
  });
}

export default function ControlResults({
  result,
  instruments,
  gross: selectedGross,
  onGrossChange,
  userWeights,
  sensitivityBusy,
  onCalculateSensitivity,
}: {
  result: any;
  instruments: Instrument[];
  gross?: boolean;
  onGrossChange?: (value: boolean) => void;
  userWeights?: Record<string, number> | null;
  sensitivityBusy?: boolean;
  onCalculateSensitivity?: () => void;
}) {
  const [localGross, setLocalGross] = useState(false);
  const [sensitivityAsset, setSensitivityAsset] = useState<string | null>(null);
  const gross = selectedGross ?? localGross;
  const setGross = onGrossChange ?? setLocalGross;
  const c = result.portfolio_controls;
  if (!c) return null;
  const side = gross ? "gross" : "net";
  const name = (a: string) =>
    a === "BIL_REFERENCE" ? "BIL" : ticker(a, instruments);
  const portfolioSensitivity: TrendSensitivityPoint[] =
    c.sensitivity?.scope === "common_lookback" ? c.sensitivity.points : [];
  const byAsset: Record<string, AssetTrendSensitivityResult> =
    result.asset_trend_sensitivity ?? {};
  const trendAssets = (c.assets as string[]).filter((asset) => {
    const indicator = c.config.trend?.asset_rules?.[asset]?.indicator;
    return (
      (userWeights?.[asset] ?? 0) > 0 &&
      (indicator === "sma" || indicator === "momentum")
    );
  });
  const pages = [null, ...trendAssets.filter((asset) => byAsset[asset])];
  const page = Math.max(0, pages.indexOf(sensitivityAsset));
  const activeAsset = pages[page];
  const assetSeries = activeAsset === null ? undefined : byAsset[activeAsset];
  const sensitivity = assetSeries?.points ?? portfolioSensitivity;
  const regression = sensitivityRegression(sensitivity, side);
  const annualCommission = (path: any): number | null => {
    if (
      !Array.isArray(path.events) ||
      !Array.isArray(path.positions) ||
      !c.months?.length
    )
      return null;
    const years = new Map<string, number>(
      c.months.map((month: string) => [month.slice(0, 4), 0]),
    );
    for (const event of path.events) {
      const month = c.months[Math.max(0, event.month_index)];
      if (!month) continue;
      const positions = path.positions[event.month_index + 1];
      const before =
        event.month_index < 0
          ? 1
          : Array.isArray(positions)
            ? positions.reduce((sum: number, value: number) => sum + value, 0) +
              event.commissions
            : 0;
      if (before > 0)
        years.set(
          month.slice(0, 4),
          (years.get(month.slice(0, 4)) ?? 0) + event.commissions / before,
        );
    }
    return years.size
      ? [...years.values()].reduce((a, b) => a + b, 0) / years.size
      : null;
  };
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
              "average_annual_commission",
              "trade_count",
              "rebalancing_events",
              "annualized_turnover",
            ].map((k) => (
              <tr key={k}>
                <td>{labels[k] ?? k}</td>
                <td>
                  {k === "average_annual_commission"
                    ? percent(annualCommission(c.baseline), 4)
                    : metric(k, c.baseline[k])}
                </td>
                <td>
                  {k === "average_annual_commission"
                    ? percent(annualCommission(c.controlled), 4)
                    : metric(k, c.controlled[k])}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">
        Среднегодовые комиссии: сумма долей капитала перед каждой сделкой за
        календарный год, затем среднее по годам выбранного периода, включая
        неполные годы и первоначальную покупку.
      </p>
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
        title="Просадки: исходный и управляемый"
        data={["baseline", "controlled"].map((k) => ({
          type: "scatter",
          mode: "lines",
          name: k === "baseline" ? "Исходный" : "Управляемый",
          x: [c.months[0], ...c.months],
          y: drawdowns(c[k][`${side}_wealth`]),
          hovertemplate: "%{fullData.name}<br>%{x}: %{y:.2%}<extra></extra>",
        }))}
        layout={{ yaxis: { title: { text: "Просадка" }, tickformat: ".0%" } }}
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
            hovertemplate: `${escapeHtml(name(a))}<br>%{x}: %{customdata:.2%}<extra></extra>`,
          }))}
        layout={{ yaxis: { tickformat: ".0%" } }}
      />
      {c.config.mode === "downside_vol" && (
        <>
          <p className="hint">
            Нисходящая волатильность годовая: рассчитана по последним{" "}
            {c.config.downside_vol.lookback_months} месяцам исходного портфеля с
            фиксированными весами. Alpha — доля исходного портфеля на следующий
            месяц.
          </p>
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
        </>
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
      {portfolioSensitivity.length > 0 && (
        <>
          <h4>Чувствительность к периоду SMA/Momentum</h4>
          {onCalculateSensitivity &&
            trendAssets.some((asset) => !byAsset[asset]) && (
              <button
                type="button"
                disabled={sensitivityBusy}
                onClick={onCalculateSensitivity}
              >
                Рассчитать для активов
              </button>
            )}
          {pages.length > 1 && (
            <div
              className="sensitivity-actions"
              aria-label="Выбор графика чувствительности"
            >
              <button
                type="button"
                aria-label="Предыдущий график"
                onClick={() =>
                  setSensitivityAsset(
                    pages[(page - 1 + pages.length) % pages.length],
                  )
                }
              >
                ←
              </button>
              <strong aria-live="polite">
                {activeAsset === null ? "Весь портфель" : name(activeAsset)}
              </strong>
              <button
                type="button"
                aria-label="Следующий график"
                onClick={() =>
                  setSensitivityAsset(pages[(page + 1) % pages.length])
                }
              >
                →
              </button>
            </div>
          )}
          <p className="hint">
            {assetSeries
              ? `${name(assetSeries.asset_id)}: 100% начального веса, ${assetSeries.indicator === "sma" ? "SMA" : "Momentum"}. При выходе по сигналу капитал переходит в заданный хедж.`
              : "Для каждой точки один период применяется ко всем активам с включённым SMA или Momentum."}{" "}
            Число над точкой — период в месяцах.
          </p>
          <p>
            Корреляция Пирсона (r): {number(regression?.correlation)}
            {regression?.correlation != null
              ? ` · R²: ${number(regression.correlation ** 2)}`
              : " · недостаточно различающихся данных"}
          </p>
          <Plot
            title="Чувствительность к периоду SMA/Momentum"
            data={[
              {
                type: "scatter",
                mode: "text+markers",
                x: sensitivity.map((p) => p[side].maximum_drawdown),
                y: sensitivity.map((p) => p[side].annualized_cagr),
                text: sensitivity.map((p) => String(p.lookback_months)),
                textposition: "top center",
                cliponaxis: false,
                marker: { color: "#0072b2", size: 8 },
                hovertemplate:
                  "Период: %{text} мес.<br>Максимальная просадка: %{x:.2%}<br>CAGR: %{y:.2%}<extra></extra>",
              },
              ...(regression
                ? [
                    {
                      type: "scatter" as const,
                      mode: "lines" as const,
                      name: "Линейная регрессия",
                      x: regression.x,
                      y: regression.y,
                      line: {
                        color: "#c0873a",
                        width: 2,
                        dash: "dash" as const,
                      },
                      hovertemplate:
                        "Линейная регрессия<br>Максимальная просадка: %{x:.2%}<br>CAGR: %{y:.2%}<extra></extra>",
                    },
                  ]
                : []),
            ]}
            layout={{
              xaxis: {
                title: { text: "Максимальная просадка портфеля" },
                tickformat: ".1%",
              },
              yaxis: { title: { text: "CAGR портфеля" }, tickformat: ".1%" },
            }}
          />
        </>
      )}
      {c.sensitivity && c.sensitivity.scope !== "common_lookback" && (
        <p className="hint">
          Чтобы увидеть чувствительность к общему периоду SMA/Momentum,
          повторите расчёт.
        </p>
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
