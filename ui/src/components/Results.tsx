import { useMemo, useState } from "react";
import type { Data, PlotMouseEvent } from "plotly.js";
import type {
  BootstrapResult,
  Calculation,
  CVResult,
  FrontierResult,
  Instrument,
  Portfolio,
  ResampledResult,
  EfficiencyDistance,
  EfficiencyDistribution,
  RollingEfficiencyResult,
} from "../types";
import { METHODS } from "../types";
import { compatible } from "../lib/snapshot";
import {
  displayedVolatility,
  escapeHtml as escape,
  number,
  percent,
  ticker,
  visibleWeights,
} from "../lib/display";
import Plot from "./Plot";
import PortfolioTable from "./PortfolioTable";

const AXES = {
  xaxis: { title: { text: "Годовая волатильность" }, tickformat: ".1%" },
  yaxis: {
    title: { text: "Ожидаемая доходность (арифм.)" },
    tickformat: ".1%",
  },
};
function hover(p: Portfolio, label: string, instruments: Instrument[]) {
  const m = p.metrics;
  return `<b>${escape(label)}</b><br>Ожидаемая (арифм.) ${percent(m?.expected_return)}<br>Историческая CAGR ${percent(m?.historical_cagr)}<br>Волатильность ${percent(displayedVolatility(m))}<br>Estimated Sharpe ${number(m?.sharpe_ratio)}<br>Historical Sharpe ${number(m?.historical_sharpe_ratio)}<br>${visibleWeights(
    p.weights ?? {},
  )
    .map(([a, w]) => `${escape(ticker(a, instruments))}: ${percent(w)}`)
    .join("<br>")}<extra></extra>`;
}
const EFFICIENCY_LABELS = {
  heading:
    "\u0423\u0441\u0442\u043e\u0439\u0447\u0438\u0432\u043e\u0441\u0442\u044c \u043f\u043e\u0440\u0442\u0444\u0435\u043b\u044f",
  retrospective:
    "\u0413\u0440\u0430\u043d\u0438\u0446\u0430 \u043e\u0446\u0435\u043d\u0438\u0432\u0430\u0435\u0442\u0441\u044f \u0440\u0435\u0442\u0440\u043e\u0441\u043f\u0435\u043a\u0442\u0438\u0432\u043d\u043e \u0434\u043b\u044f \u043a\u0430\u0436\u0434\u043e\u0433\u043e \u043e\u043a\u043d\u0430/\u0441\u0446\u0435\u043d\u0430\u0440\u0438\u044f.",
  shortfall:
    "\u0423\u043f\u0443\u0449\u0435\u043d\u043d\u0430\u044f \u0433\u043e\u0434\u043e\u0432\u0430\u044f \u0434\u043e\u0445\u043e\u0434\u043d\u043e\u0441\u0442\u044c, \u043f.\u043f.",
  excessRisk:
    "\u0418\u0437\u0431\u044b\u0442\u043e\u0447\u043d\u0430\u044f \u0433\u043e\u0434\u043e\u0432\u0430\u044f \u0432\u043e\u043b\u0430\u0442\u0438\u043b\u044c\u043d\u043e\u0441\u0442\u044c, \u043f.\u043f.",
  counts:
    "\u0423\u0441\u043f\u0435\u0448\u043d\u043e / \u043e\u0448\u0438\u0431\u043a\u0438",
  rolling:
    "\u0421\u043a\u043e\u043b\u044c\u0437\u044f\u0449\u0438\u0435 10-\u043b\u0435\u0442\u043d\u0438\u0435 \u043e\u043a\u043d\u0430",
};
function ResultStatus({
  status,
  diagnostics,
}: {
  status: string;
  diagnostics?: Record<string, unknown>;
}) {
  if (status === "optimal") return <>{status}</>;
  const details = diagnostics ?? {};
  const detail = [
    details.reason,
    details.return_error,
    details.risk_error,
    details.solver_status,
    details.return_solver,
    details.risk_solver,
  ]
    .filter(
      (item): item is string => typeof item === "string" && item.length > 0,
    )
    .join("; ");
  const explanation = /singular or nearly singular/i.test(detail)
    ? `Матрица ковариации вырождена или близка к вырожденной: доходности активов линейно зависимы, например когда один из активов почти не менялся. Попробуйте Sample Covariance или Ledoit-Wolf. (${detail})`
    : detail || "Причина ошибки не была передана в диагностике этого расчёта.";
  return (
    <span className="failure-status" tabIndex={0} title={explanation}>
      {status}
    </span>
  );
}
function formatEfficiencySummary(
  v: EfficiencyDistribution["return_shortfall"],
) {
  return v
    ? `mean ${(v.mean * 100).toFixed(2)} п.п.; median ${(v.median * 100).toFixed(2)}; P5-P95 ${(v.p5 * 100).toFixed(2)}-${(v.p95 * 100).toFixed(2)}; max ${(v.maximum * 100).toFixed(2)}`
    : "-";
}
function EfficiencyPanel({
  value,
  title,
  instruments = [],
  showWeights = true,
}: {
  value?: EfficiencyDistribution | null;
  title: string;
  instruments?: Instrument[];
  showWeights?: boolean;
}) {
  const [metric, setMetric] = useState<
    "return_shortfall" | "excess_volatility"
  >("return_shortfall");
  if (!value) return null;
  const isShortfall = metric === "return_shortfall";
  const metricLabel = isShortfall
    ? EFFICIENCY_LABELS.shortfall
    : EFFICIENCY_LABELS.excessRisk;
  const distributionTitle = isShortfall
    ? "Распределение упущенной годовой доходности"
    : "Распределение избыточной годовой волатильности";
  const histogram =
    value.results && value.results.length > 0
      ? ({
          type: "histogram",
          x: value.results
            .filter((r) => r.status === "optimal" && r[metric] != null)
            .map((r) => r[metric]! * 100),
          nbinsx: 30,
          name: "Bootstrap",
          hovertemplate: `<b>${metricLabel}</b><br>Середина интервала: %{x} п.п.<br>Число выборок: %{y}<extra></extra>`,
        } as Data)
      : null;
  return (
    <section className="subsection">
      <h3>
        {EFFICIENCY_LABELS.heading} - {title}
      </h3>
      {showWeights && (
        <p className="hint">
          Ваш портфель (веса одинаковы во всех выборках):{" "}
          {visibleWeights(value.user_weights)
            .map(([a, w]) => `${ticker(a, instruments)} ${percent(w)}`)
            .join(" - ")}
        </p>
      )}
      <p className="hint">{EFFICIENCY_LABELS.retrospective}</p>
      <div className="metrics efficiency-metrics">
        <span>
          {EFFICIENCY_LABELS.shortfall}
          <b className="efficiency-summary">
            {formatEfficiencySummary(value.return_shortfall)}
          </b>
        </span>
        <span>
          {EFFICIENCY_LABELS.excessRisk}
          <b className="efficiency-summary">
            {formatEfficiencySummary(value.excess_volatility)}
          </b>
        </span>
        <span>
          {EFFICIENCY_LABELS.counts}
          <b>
            {value.valid_count} / {value.failed_count}
          </b>
        </span>
      </div>
      {histogram && (
        <>
          <div
            className="segmented"
            role="group"
            aria-label="Показатель гистограммы"
          >
            <button
              type="button"
              className={isShortfall ? "active" : ""}
              aria-pressed={isShortfall}
              onClick={() => setMetric("return_shortfall")}
            >
              Распределение упущенной годовой доходности
            </button>
            <button
              type="button"
              className={!isShortfall ? "active" : ""}
              aria-pressed={!isShortfall}
              onClick={() => setMetric("excess_volatility")}
            >
              Распределение избыточной годовой волатильности
            </button>
          </div>
          <h4>{distributionTitle}</h4>
          <p className="hint">
            {isShortfall
              ? "Число выборок в каждом диапазоне упущенной доходности при риске не выше риска вашего портфеля."
              : "Число выборок в каждом диапазоне избыточной волатильности при доходности не ниже доходности вашего портфеля."}
            {value.horizon &&
              ` Горизонт каждой выборки: ${value.horizon.months} мес. (${number(value.horizon.years, 2)} лет).`}
          </p>
          <Plot
            title={`Bootstrap: ${metricLabel}${value.horizon ? ` - ${value.horizon.months} мес. (${number(value.horizon.years, 2)} лет)` : ""}`}
            data={[histogram]}
            layout={{
              xaxis: { title: { text: metricLabel } },
              yaxis: { title: { text: "Число выборок" }, rangemode: "tozero" },
            }}
          />
        </>
      )}
      {value.results && value.results.length <= 50 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{"\u041e\u043a\u043d\u043e / \u0444\u043e\u043b\u0434"}</th>
                <th>
                  {
                    "\u0414\u043e\u0445\u043e\u0434\u043d\u043e\u0441\u0442\u044c / \u0432\u043e\u043b\u0430\u0442\u0438\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u043f\u043e\u0440\u0442\u0444\u0435\u043b\u044f"
                  }
                </th>
                <th>{EFFICIENCY_LABELS.shortfall}</th>
                <th>{EFFICIENCY_LABELS.excessRisk}</th>
                <th>{"\u0421\u0442\u0430\u0442\u0443\u0441"}</th>
              </tr>
            </thead>
            <tbody>
              {value.results.map((r, i) => (
                <tr key={i}>
                  <td>
                    {r.start_month
                      ? `${r.start_month} - ${r.end_month}`
                      : (r.fold ?? r.iteration ?? i + 1)}
                  </td>
                  <td>
                    {r.user_expected_return == null
                      ? "-"
                      : `${percent(r.user_expected_return)} / ${percent(r.user_volatility)}`}
                  </td>
                  <td>
                    {r.return_shortfall == null
                      ? "-"
                      : (r.return_shortfall * 100).toFixed(3)}
                  </td>
                  <td>
                    {r.excess_volatility == null
                      ? "-"
                      : (r.excess_volatility * 100).toFixed(3)}
                  </td>
                  <td>
                    <ResultStatus
                      status={r.status}
                      diagnostics={r.diagnostics}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function BootstrapPortfolioMetrics({ result }: { result: BootstrapResult }) {
  const robustness = result.efficiency_robustness;
  if (!robustness?.distributions || !robustness.horizon) return null;
  const labels: Record<string, string> = {
    annualized_cagr: "CAGR, % годовых",
    annualized_arithmetic_return: "Арифметическая доходность, % годовых",
    annualized_volatility: "Годовая волатильность (историческая), %",
    maximum_drawdown: "Максимальная просадка за горизонт, %",
    historical_sharpe_ratio: "Исторический Sharpe",
    terminal_wealth: "Стоимость из $1 к концу горизонта",
    return_shortfall: EFFICIENCY_LABELS.shortfall,
    excess_volatility: EFFICIENCY_LABELS.excessRisk,
  };
  const format = (key: string, value: number | null) => {
    if (value == null) return "—";
    if (key === "historical_sharpe_ratio") return number(value);
    if (key === "terminal_wealth") return `$${number(value, 3)}`;
    if (key === "return_shortfall" || key === "excess_volatility")
      return `${(value * 100).toFixed(2)} п.п.`;
    return percent(value);
  };
  return (
    <section className="subsection">
      <h3>Показатели фиксированного портфеля</h3>
      <p className="hint">
        Горизонт каждой выборки: {robustness.horizon.months} мес. (
        {number(robustness.horizon.years, 2)} лет) ·{" "}
        {result.return_basis === "real" ? "Real" : "Nominal"} USD. Полная длина
        выбранной истории, ежемесячное восстановление заданных весов.
      </p>
      <p className="hint">
        P5/P95 описывают исторические бутстрап-сценарии и не являются
        калиброванными вероятностями будущей доходности. В Real стоимость $1
        выражена в покупательной способности начала периода. Для каждого
        показателя указано число доступных значений.
      </p>
      <div className="table-scroll">
        <table aria-label="Распределения показателей фиксированного портфеля">
          <thead>
            <tr>
              <th>Показатель</th>
              <th>Горизонт</th>
              <th>Среднее</th>
              <th>Медиана</th>
              <th>P5</th>
              <th>P95</th>
              <th>Min</th>
              <th>Max</th>
              <th>Доступно / всего</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(robustness.distributions).map(([key, d]) => (
              <tr key={key}>
                <td>{labels[key] ?? key}</td>
                <td>
                  {d.horizon_months} мес. ({number(d.horizon_years, 2)} лет)
                </td>
                {(
                  ["mean", "median", "p5", "p95", "minimum", "maximum"] as const
                ).map((stat) => (
                  <td key={stat}>{format(key, d[stat])}</td>
                ))}
                <td>
                  {d.valid_count} / {d.valid_count + d.unavailable_count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function RollingEfficiencyView({
  result,
  instruments = [],
}: {
  result: RollingEfficiencyResult;
  instruments?: Instrument[];
}) {
  const [metric, setMetric] = useState<
    "return_shortfall" | "excess_volatility"
  >("return_shortfall");
  const windows = result.windows;
  const chartTitle =
    metric === "return_shortfall"
      ? EFFICIENCY_LABELS.shortfall
      : EFFICIENCY_LABELS.excessRisk;
  const chart = (
    key: "return_shortfall" | "excess_volatility",
    title: string,
  ): Data => ({
    type: "scatter",
    mode: "lines+markers",
    name: title,
    x: windows.map((w) => w.end_month),
    y: windows.map((w) =>
      w.status !== "optimal" || w[key] == null ? null : w[key]! * 100,
    ),
    customdata: windows.map((w) => [w.start_month, w.end_month]),
    hovertemplate: `<b>${title}</b><br>Период: %{customdata[0]} — %{customdata[1]}<br>Значение: %{y:.2f} п.п.<extra></extra>`,
    connectgaps: false,
  });
  return (
    <section className="subsection">
      <h3>
        {EFFICIENCY_LABELS.heading} - {EFFICIENCY_LABELS.rolling}
      </h3>
      <p className="hint">
        {result.return_basis === "real" ? "Real" : "Nominal"} USD -{" "}
        {result.period.start} - {result.period.end}
      </p>
      <p className="hint">
        Ваш портфель (веса одинаковы во всех окнах):{" "}
        {visibleWeights(result.user_weights)
          .map(([a, w]) => {
            const symbol =
              instruments.find((item) => String(item.id) === a)?.ticker ??
              result.source_metadata.find(
                (item) => String(item.instrument_id) === a,
              )?.ticker ??
              `Актив ${a}`;
            return `${symbol} ${percent(w)}`;
          })
          .join(" - ")}
        . {EFFICIENCY_LABELS.retrospective}
      </p>
      <div className="metrics efficiency-metrics">
        <span>
          {EFFICIENCY_LABELS.shortfall}
          <b className="efficiency-summary">
            {formatEfficiencySummary(result.summary.return_shortfall)}
          </b>
        </span>
        <span>
          {EFFICIENCY_LABELS.excessRisk}
          <b className="efficiency-summary">
            {formatEfficiencySummary(result.summary.excess_volatility)}
          </b>
        </span>
        <span>
          {EFFICIENCY_LABELS.counts}
          <b>
            {result.summary.valid_count} / {result.summary.failed_count}
          </b>
        </span>
      </div>
      <div className="segmented" role="group" aria-label="Показатель графика">
        <button
          type="button"
          className={metric === "return_shortfall" ? "active" : ""}
          aria-pressed={metric === "return_shortfall"}
          onClick={() => setMetric("return_shortfall")}
        >
          Упущенная годовая доходность
        </button>
        <button
          type="button"
          className={metric === "excess_volatility" ? "active" : ""}
          aria-pressed={metric === "excess_volatility"}
          onClick={() => setMetric("excess_volatility")}
        >
          Избыточная годовая волатильность
        </button>
      </div>
      <h4>{chartTitle}</h4>
      <p className="hint">
        {metric === "return_shortfall"
          ? "На сколько выше могла быть годовая доходность при волатильности не выше волатильности вашего портфеля и заданных ограничениях."
          : "На сколько ниже могла быть годовая волатильность при доходности не ниже доходности вашего портфеля и заданных ограничениях."}
      </p>
      <Plot
        title={chartTitle}
        data={[chart(metric, chartTitle)]}
        layout={{
          xaxis: { title: { text: "Конец 10-летнего окна" } },
          yaxis: { title: { text: "п.п." } },
        }}
      />
    </section>
  );
}
function trace(
  points: Portfolio[],
  label: string,
  color: string,
  instruments: Instrument[],
  line = false,
): Data {
  const good = points.filter((p) => p.metrics && p.weights);
  return {
    type: "scatter",
    mode: line ? "lines+markers" : "markers",
    name: label,
    x: good.map((p) => displayedVolatility(p.metrics) ?? null),
    y: good.map((p) => p.metrics!.expected_return),
    marker: { color, size: line ? 4 : 12 },
    line: { color, width: 2.5 },
    customdata: good as never,
    hovertemplate: good.map((p) => hover(p, label, instruments)),
  };
}
function assetTrace(
  portfolios: Record<string, Portfolio> | undefined,
  instruments: Instrument[],
): Data[] {
  if (!portfolios || !Object.keys(portfolios).length) return [];
  const entries = Object.entries(portfolios).filter(
    ([, p]) => p.metrics && p.weights,
  );
  return [
    {
      type: "scatter",
      mode: "text+markers",
      name: "Активы",
      x: entries.map(([, p]) => displayedVolatility(p.metrics) ?? null),
      y: entries.map(([, p]) => p.metrics!.expected_return),
      text: entries.map(([id]) => ticker(id, instruments)),
      textposition: "top center",
      textfont: { color: "#d14b74", size: 11 },
      marker: {
        color: "#d14b74",
        symbol: "diamond",
        size: 12,
        line: { color: "#fff", width: 1 },
      },
      customdata: entries.map(([, p]) => p) as never,
      hovertemplate: entries.map(([id, p]) =>
        hover(p, ticker(id, instruments), instruments),
      ),
    },
  ];
}
export function PointDetail({
  selected,
  instruments,
}: {
  selected: { label: string; portfolio: Portfolio } | null;
  instruments: Instrument[];
}) {
  if (!selected)
    return (
      <div className="point-detail muted">
        Выберите точку на графике или портфель в таблице, чтобы увидеть
        распределение.
      </div>
    );
  const { portfolio: p, label } = selected;
  return (
    <div className="point-detail">
      <h3>{label}</h3>
      <div className="metrics">
        <span>
          Ожидаемая (арифм.)<b>{percent(p.metrics?.expected_return)}</b>
        </span>
        <span>
          Волатильность<b>{percent(displayedVolatility(p.metrics))}</b>
        </span>
        <span>
          Estimated Sharpe<b>{number(p.metrics?.sharpe_ratio)}</b>
          <small>
            Historical Sharpe {number(p.metrics?.historical_sharpe_ratio)}
          </small>
        </span>
        <span>
          Историческая CAGR<b>{percent(p.metrics?.historical_cagr)}</b>
        </span>
      </div>
      <div className="allocations">
        {visibleWeights(p.weights).map(([a, w]) => (
          <div key={a}>
            <span>
              {ticker(a, instruments)} <b>{percent(w)}</b>
            </span>
            <div className="allocation-track">
              <i style={{ width: `${w * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
      {p.feasible === false && (
        <p className="warning">
          Unconstrained reference: нарушает заданные ограничения.
        </p>
      )}
    </div>
  );
}
export function FrontierView({
  calculation,
  related,
  instruments,
  compare = false,
}: {
  calculation: Calculation;
  related: Calculation[];
  instruments: Instrument[];
  compare?: boolean;
}) {
  const [selected, setSelected] = useState<{
    label: string;
    portfolio: Portfolio;
  } | null>(null);
  const result = calculation.result as FrontierResult;
  const rows = useMemo(() => {
    if (compare)
      return related
        .filter((c) => c.kind === "frontier" && compatible(calculation, c))
        .flatMap((c) => {
          const r = c.result as FrontierResult;
          const name = METHODS.find(
            (m) => m.value === c.request.covariance_method,
          )!.short;
          return [
            { label: `${name} · GMV`, portfolio: r.gmv },
            { label: `${name} · Max Sharpe`, portfolio: r.max_sharpe },
            ...(r.user_portfolio
              ? [
                  {
                    label: `${name} · Ваш портфель`,
                    portfolio: r.user_portfolio,
                  },
                ]
              : []),
          ];
        });
    const rows = [
      { label: "GMV", portfolio: result.gmv },
      {
        label: result.max_sharpe.label ?? "Max Sharpe",
        portfolio: result.max_sharpe,
      },
      { label: "Equal Weight", portfolio: result.equal_weight },
      ...(result.user_portfolio
        ? [{ label: "Ваш портфель", portfolio: result.user_portfolio }]
        : []),
    ];
    for (const c of related.filter(
      (c) =>
        compatible(calculation, c) &&
        c.request.covariance_method === calculation.request.covariance_method,
    )) {
      if (c.kind === "cross-validation") {
        const r = c.result as CVResult;
        if (r.ensemble)
          rows.push({ label: "CV Ensemble", portfolio: r.ensemble });
      }
      if (c.kind === "bootstrap")
        for (const [o, r] of Object.entries(
          (c.result as BootstrapResult).objectives,
        ))
          if (r.resampled)
            rows.push({ label: `Resampled ${o}`, portfolio: r.resampled });
    }
    return rows;
  }, [calculation, related, result, compare]);
  const data = useMemo(() => {
    const curves = compare
      ? related.filter(
          (c) => c.kind === "frontier" && compatible(calculation, c),
        )
      : [calculation];
    const d = curves.map((c) => {
      const m = METHODS.find((m) => m.value === c.request.covariance_method)!;
      return trace(
        (c.result as FrontierResult).frontier,
        m.short,
        m.color,
        instruments,
        true,
      );
    });
    if (!compare)
      for (const c of related.filter(
        (c) =>
          c.kind === "resampled-frontier" &&
          compatible(calculation, c) &&
          c.request.covariance_method === calculation.request.covariance_method,
      ))
        d.push({
          ...trace(
            (c.result as ResampledResult).frontier,
            "Resampled Frontier",
            "#a88b61",
            instruments,
            true,
          ),
          line: { color: "#a88b61", dash: "dash" },
        } as Data);
    rows.forEach((row, i) =>
      d.push(
        trace(
          [row.portfolio],
          row.label,
          ["#173847", "#c0873a", "#85929a", "#548b79", "#8c7499"][i % 5],
          instruments,
          false,
        ),
      ),
    );
    d.push(...assetTrace(result.asset_portfolios, instruments));
    return d;
  }, [
    calculation,
    related,
    compare,
    instruments,
    rows,
    result.asset_portfolios,
  ]);
  const click = (e: PlotMouseEvent) => {
    const p = e.points[0];
    if (p?.customdata)
      setSelected({
        label:
          p.data.name === "Активы"
            ? ticker(
                String(
                  (p.customdata as unknown as Portfolio).diagnostics.asset_id,
                ),
                instruments,
              )
            : (p.data.name ?? "Frontier point"),
        portfolio: p.customdata as unknown as Portfolio,
      });
  };
  return (
    <>
      <Plot
        title={compare ? "Сравнение методов ковариации" : "Efficient Frontier"}
        data={data}
        layout={AXES}
        onSelect={click}
        height={490}
      />
      <PointDetail selected={selected} instruments={instruments} />
      <PortfolioTable
        rows={rows}
        instruments={instruments}
        onSelect={(portfolio, label) => setSelected({ portfolio, label })}
      />
      {compare && (
        <p className="hint">
          На графике только расчёты с одинаковыми данными, датами и
          ограничениями. Цвет кривой соответствует методу; розовые ромбы —
          отдельные активы.
        </p>
      )}
      <p className="hint">
        Доходность — арифметическая оценка, CAGR — историческое накопление. CV
        Ensemble и Resampled оценены на исходной полной выборке, а не независимо
        out-of-sample. Resampled Frontier — отдельная усреднённая кривая.
      </p>
    </>
  );
}
export function CVView({
  result: r,
  instruments,
}: {
  result: CVResult;
  instruments: Instrument[];
}) {
  const userEfficiency =
    r.efficiency_robustness &&
    Object.values(r.efficiency_robustness.user_weights ?? {}).some(
      (weight) => weight > 0,
    )
      ? r.efficiency_robustness
      : null;
  const data = useMemo(
    () =>
      r.assets.map(
        (a, i) =>
          ({
            type: "bar",
            name: ticker(a, instruments),
            x: [...r.folds.map((f) => `Fold ${f.fold}`), "Ensemble"],
            y: [
              ...r.folds.map((f) => f.weights?.[a] ?? null),
              r.ensemble?.weights?.[a] ?? null,
            ],
            marker: {
              color: ["#256478", "#b98943", "#7b7099", "#579b7d"][i % 4],
            },
            hovertemplate: "%{x}<br>%{y:.2%}<extra>%{fullData.name}</extra>",
          }) as Data,
      ),
    [r, instruments],
  );
  return (
    <>
      <p className="hint">
        Двунаправленная односторонняя блочная CV: обучение одним непрерывным
        периодом до или после проверки. Веса фиксированы, разрыва нет.
        Диагностика чувствительности к выборке и устойчивости аллокации; метрики
        проверки не являются историческим торговым бэктестом или независимым
        forward out-of-sample результатом. Проверка: {r.validation_observations}{" "}
        из {r.total_observations} мес. (
        {percent(r.validation_coverage_fraction)}).
        {r.validation_coverage_fraction < 1 &&
          " Центральные месяцы не входят в проверку при K=2."}
      </p>
      <Plot
        title="Веса CV-фолдов"
        data={data}
        layout={{
          barmode: "stack",
          yaxis: { tickformat: ".0%", title: { text: "Вес" } },
        }}
      />
      {userEfficiency && (
        <div className="metrics efficiency-metrics cv-efficiency-summary">
          <span>
            Упущенная годовая доходность (указанного портфеля), п.п.
            <b className="efficiency-summary">
              {formatEfficiencySummary(userEfficiency.return_shortfall)}
            </b>
          </span>
          <span>
            Избыточная годовая волатильность (указанного портфеля), п.п.
            <b className="efficiency-summary">
              {formatEfficiencySummary(userEfficiency.excess_volatility)}
            </b>
          </span>
        </div>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Fold</th>
              <th>Обучение</th>
              <th>Проверка</th>
              <th>Веса</th>
              <th>Train arithmetic return / vol</th>
              <th>Validation arithmetic return / vol</th>
              <th>Historical Validation Sharpe</th>
              {userEfficiency && (
                <th>Your Port on Valid arithmetic return / vol</th>
              )}
            </tr>
          </thead>
          <tbody>
            {r.folds.map((f) => (
              <tr key={f.fold}>
                <td>
                  {f.fold}
                  <small>
                    <ResultStatus
                      status={f.status}
                      diagnostics={f.diagnostics}
                    />
                  </small>
                </td>
                <td>
                  {f.training.observations} мес.
                  <small>
                    {f.training_direction === "train_before_validation"
                      ? "До проверки"
                      : "После проверки"}
                  </small>
                  <small>
                    {f.training.segments
                      ?.map((s) => `${s.start}—${s.end}`)
                      .join("; ")}
                  </small>
                </td>
                <td>
                  {f.validation.start}—{f.validation.end}
                  <small>{f.validation.observations} мес.</small>
                </td>
                <td>
                  {visibleWeights(f.weights).map(([a, w]) => (
                    <small key={a}>
                      {ticker(a, instruments)} {percent(w)}
                    </small>
                  ))}
                </td>
                <td>
                  {percent(f.training_metrics?.expected_return)} /{" "}
                  {percent(f.training_metrics?.volatility)}
                </td>
                <td>
                  {percent(
                    f.validation_metrics?.realized_annual_arithmetic_return,
                  )}{" "}
                  / {percent(f.validation_metrics?.realized_annual_volatility)}
                </td>
                <td>{number(f.validation_metrics?.realized_sharpe_ratio)}</td>
                {userEfficiency && (
                  <td>
                    {f.efficiency_robustness?.user_expected_return != null &&
                    f.efficiency_robustness.user_volatility != null ? (
                      `${percent(f.efficiency_robustness.user_expected_return)} / ${percent(f.efficiency_robustness.user_volatility)}`
                    ) : f.efficiency_robustness?.status &&
                      f.efficiency_robustness.status !== "optimal" ? (
                      <ResultStatus
                        status={f.efficiency_robustness.status}
                        diagnostics={f.efficiency_robustness.diagnostics}
                      />
                    ) : (
                      "—"
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {r.stability && (
        <>
          <h3>Устойчивость весов</h3>
          <div className="metrics">
            <span>
              Среднее отклонение
              <b>{percent(r.stability.mean_absolute_weight_deviation)}</b>
            </span>
            <span>
              Макс. отклонение
              <b>{percent(r.stability.maximum_weight_deviation)}</b>
            </span>
            <span>
              Средний turnover
              <b>
                {percent(r.stability.mean_allocation_turnover_from_original)}
              </b>
            </span>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Актив</th>
                  <th>SD</th>
                  <th>Min</th>
                  <th>Max</th>
                  <th>На нижней границе</th>
                  <th>На верхней</th>
                </tr>
              </thead>
              <tbody>
                {r.assets.map((a) => (
                  <tr key={a}>
                    <td>{ticker(a, instruments)}</td>
                    {[
                      r.stability!.standard_deviation,
                      r.stability!.minimum,
                      r.stability!.maximum,
                      r.stability!.lower_bound_frequency,
                      r.stability!.upper_bound_frequency,
                    ].map((v, i) => (
                      <td key={i}>{percent(v[a])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <h3>Сравнение на полной выборке</h3>
      <PortfolioTable
        rows={[
          { label: "Original", portfolio: r.original },
          ...(r.ensemble
            ? [{ label: "CV Ensemble", portfolio: r.ensemble }]
            : []),
        ]}
        instruments={instruments}
      />
      <p className="hint">
        Статистики Ensemble рассчитаны по исходной полной выборке; независимая
        проверка показана отдельно в строках фолдов.
      </p>
    </>
  );
}
export function BootstrapView({
  result: r,
  resampled,
  instruments,
}: {
  result: BootstrapResult;
  resampled?: ResampledResult;
  instruments: Instrument[];
}) {
  const [objective, setObjective] = useState("gmv");
  const o = r.objectives[objective] ?? Object.values(r.objectives)[0];
  const isTarget = o.target_return != null;
  const assets = r.assets,
    stats = o.stability?.assets;
  const data = useMemo(() => {
    if (!stats) return [];
    const labels = assets.map((a) => ticker(a, instruments));
    return [
      {
        type: "bar",
        name: "Original",
        x: labels,
        y: assets.map((a) => stats[a].original),
        marker: { color: "#256478" },
      },
      {
        type: "bar",
        name: "Bootstrap mean",
        x: labels,
        y: assets.map((a) => stats[a].mean),
        marker: { color: "#b98943" },
      },
      ...assets.map((a) => ({
        type: "scatter",
        mode: "lines+markers",
        name: "P5–P95",
        showlegend: a === assets[0],
        x: [ticker(a, instruments), ticker(a, instruments)],
        y: [stats[a].p5, stats[a].p95],
        line: { color: "#142b38", width: 3 },
        marker: { symbol: "line-ew", size: 12 },
      })),
    ] as Data[];
  }, [assets, stats, instruments]);
  const distributions = useMemo(
    () =>
      assets.map(
        (a, i) =>
          ({
            type: "histogram",
            name: ticker(a, instruments),
            x: o.iterations.filter((b) => b.weights).map((b) => b.weights![a]),
            opacity: 0.6,
            marker: {
              color: ["#256478", "#b98943", "#7b7099", "#579b7d"][i % 4],
            },
            nbinsx: 30,
          }) as Data,
      ),
    [assets, o, instruments],
  );
  return (
    <>
      <div className="segmented">
        {Object.keys(r.objectives).map((key) => (
          <button
            key={key}
            className={o === r.objectives[key] ? "active" : ""}
            onClick={() => setObjective(key)}
          >
            {key === "gmv"
              ? "GMV"
              : key === "target_return"
                ? "Target expected return"
                : "Max Sharpe"}
          </button>
        ))}
      </div>
      <EfficiencyPanel
        title="Stationary Bootstrap"
        value={r.efficiency_robustness}
        instruments={instruments}
      />
      <BootstrapPortfolioMetrics result={r} />
      {isTarget ? (
        <>
          <p title="Доля исторических бутстрап-выборок, в которых заданная ожидаемая доходность достижима при выбранных ограничениях. Не является вероятностью будущей доходности.">
            <strong>
              Достижимость целевой доходности в Bootstrap:{" "}
              {percent(o.target_feasibility_rate, 0)}
            </strong>
          </p>
          <p className={o.status === "complete" ? "hint" : "warning"}>
            Статус вычислений: {o.computational_status} · достижима{" "}
            {o.target_feasible_iterations} / {o.requested_iterations}
            {" · "}недостижима {o.target_infeasible_iterations} · численных
            ошибок {o.numerical_failure_iterations}
            {o.feasibility_unknown_iterations
              ? ` · достижимость не определена ${o.feasibility_unknown_iterations}`
              : ""}
          </p>
          {o.original_target_feasible === false && (
            <p className="hint">
              Цель недостижима на исходной выборке. Исходного целевого портфеля
              нет.
            </p>
          )}
          {o.stability && (
            <p className="hint">
              Средние веса и P5–P95 рассчитаны только по успешным выборкам с
              достижимой целью.
            </p>
          )}
          {o.numerical_failure_iterations ? (
            <p className="hint">
              Усреднение не выполнено из-за технических ошибок.
            </p>
          ) : null}
          {o.resampled_target_evaluation && (
            <p>
              Усреднённый портфель на исходной выборке:{" "}
              {percent(o.resampled_target_evaluation.expected_return)}
              {" · "}
              {o.resampled_target_evaluation.meets_target
                ? "цель выполнена"
                : "цель не выполнена"}
              {" · "}недобор{" "}
              {percent(o.resampled_target_evaluation.target_shortfall)}
              {" · "}превышение{" "}
              {percent(o.resampled_target_evaluation.target_surplus)}.
              Усреднение не гарантирует выполнения цели.
            </p>
          )}
        </>
      ) : (
        <p className={o.status === "complete" ? "hint" : "warning"}>
          Статус {o.status} · {o.successful_iterations} /{" "}
          {o.requested_iterations} успешных · ошибок {o.failed_iterations}
        </p>
      )}
      {o.failed_iterations > 0 && (
        <details open>
          <summary>Ошибки итераций</summary>
          <pre>
            {JSON.stringify(
              { reasons: o.failure_reasons, failures: o.failures },
              null,
              2,
            )}
          </pre>
        </details>
      )}
      <p className="hint">
        Геометрические блоки, совместная выборка месячных векторов, circular
        wrap. P5–P95 описывает чувствительность весов, а не будущую доходность.
        {r.conditional_bootstrap &&
          " Conditional bootstrap: shrinkage-параметры фиксированы; неопределённость их выбора не включена."}
      </p>
      {(!isTarget || stats) && (
        <Plot
          title="Bootstrap веса и P5–P95"
          data={data}
          layout={{
            barmode: "group",
            yaxis: { tickformat: ".0%", title: { text: "Вес" } },
          }}
        />
      )}
      {stats && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Актив</th>
                {[
                  "Original",
                  "Mean",
                  "Median",
                  "SD",
                  "P5",
                  "P95",
                  "Min",
                  "Max",
                  "Lower hit",
                  "Upper hit",
                ].map((k) => (
                  <th key={k}>{k}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => (
                <tr key={a}>
                  <td>{ticker(a, instruments)}</td>
                  {(
                    [
                      "original",
                      "mean",
                      "median",
                      "standard_deviation",
                      "p5",
                      "p95",
                      "minimum",
                      "maximum",
                      "lower_bound_frequency",
                      "upper_bound_frequency",
                    ] as const
                  ).map((k) => (
                    <td key={k}>{percent(stats[a][k])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {o.stability?.allocation_distance && (
        <div className="metrics">
          <span>
            Allocation distance · mean
            <b>{percent(o.stability.allocation_distance.mean)}</b>
          </span>
          <span>
            Median<b>{percent(o.stability.allocation_distance.median)}</b>
          </span>
          <span>
            P5–P95
            <b>
              {percent(o.stability.allocation_distance.p5)} —{" "}
              {percent(o.stability.allocation_distance.p95)}
            </b>
          </span>
        </div>
      )}
      {(!isTarget || stats) && (
        <Plot
          title="Распределение bootstrap весов"
          data={distributions}
          layout={{
            barmode: "overlay",
            xaxis: { tickformat: ".0%", title: { text: "Вес" } },
            yaxis: { title: { text: "Количество итераций" } },
          }}
        />
      )}
      <PortfolioTable
        rows={[
          { label: "Original", portfolio: o.original },
          ...(o.resampled
            ? [{ label: "Resampled Portfolio", portfolio: o.resampled }]
            : []),
        ]}
        instruments={instruments}
      />
      {resampled && (
        <>
          <h3>Resampled Frontier</h3>
          <p className={resampled.status === "complete" ? "hint" : "warning"}>
            {resampled.successful_iterations} / {resampled.requested_iterations}{" "}
            успешных · {resampled.failed_iterations} ошибок · {resampled.status}
            . Общие risk-aversion параметры; оценка по исходной выборке.
          </p>
          <ResampledView result={resampled} instruments={instruments} />
        </>
      )}
    </>
  );
}
export function ResampledView({
  result: r,
  instruments,
}: {
  result: ResampledResult;
  instruments: Instrument[];
}) {
  const data = useMemo(
    () => [
      trace(r.frontier, "Resampled Frontier", "#b98943", instruments, true),
      ...assetTrace(r.asset_portfolios, instruments),
      ...(r.user_portfolio?.metrics && r.user_portfolio.weights
        ? [
            trace(
              [r.user_portfolio],
              "Ваш портфель — исходная выборка",
              "#1d7880",
              instruments,
            ),
          ]
        : []),
      ...(r.user_sample_average
        ? [
            {
              type: "scatter",
              mode: "markers",
              name: "Ваш портфель — среднее Bootstrap",
              x: [r.user_sample_average.volatility],
              y: [r.user_sample_average.expected_return],
              marker: { color: "#893e88", size: 14, symbol: "star" },
              hovertemplate: `<b>Ваш портфель — среднее Bootstrap</b><br>Средняя годовая доходность: %{y:.2%}<br>Средняя годовая волатильность: %{x:.2%}<br>Успешных выборок: ${r.user_sample_average.successful_samples}<extra></extra>`,
            } as Data,
          ]
        : []),
    ],
    [r, instruments],
  );
  const [selected, setSelected] = useState<{
    label: string;
    portfolio: Portfolio;
  } | null>(null);
  return (
    <>
      <Plot
        title="Resampled Frontier"
        data={data}
        layout={AXES}
        onSelect={(e) => {
          if (e.points[0]?.customdata)
            setSelected({
              label:
                e.points[0].data.name === "Активы"
                  ? ticker(
                      String(
                        (e.points[0].customdata as unknown as Portfolio)
                          .diagnostics.asset_id,
                      ),
                      instruments,
                    )
                  : e.points[0].data.name === "Ваш портфель — исходная выборка"
                    ? "Ваш портфель — исходная выборка"
                    : "Resampled point",
              portfolio: e.points[0].customdata as unknown as Portfolio,
            });
        }}
      />
      <PointDetail selected={selected} instruments={instruments} />
      <p className="hint">
        Усреднены веса при одинаковой risk aversion. Это не точная классическая
        efficient frontier.
        {r.user_sample_average &&
          " Кривая и исходная точка оценены на полной выборке; средняя точка — среднее доходности и волатильности вашего портфеля в успешных Bootstrap-выборках."}
      </p>
      {r.failed_iterations > 0 && (
        <pre>{JSON.stringify(r.failure_reasons, null, 2)}</pre>
      )}
    </>
  );
}
