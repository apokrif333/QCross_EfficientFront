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
} from "../types";
import { METHODS } from "../types";
import { compatible } from "../lib/snapshot";
import { escapeHtml as escape, number, percent, ticker } from "../lib/display";
import Plot from "./Plot";
import PortfolioTable from "./PortfolioTable";

const AXES = {
  xaxis: { title: { text: "Годовая волатильность" }, tickformat: ".1%" },
  yaxis: { title: { text: "Ожидаемая годовая доходность" }, tickformat: ".1%" },
};
function hover(p: Portfolio, label: string, instruments: Instrument[]) {
  const m = p.metrics;
  return `<b>${escape(label)}</b><br>Доходность ${percent(m?.expected_return)}<br>Волатильность ${percent(m?.volatility)}<br>Sharpe ${number(m?.sharpe_ratio)}<br>${Object.entries(
    p.weights ?? {},
  )
    .map(([a, w]) => `${escape(ticker(a, instruments))}: ${percent(w)}`)
    .join("<br>")}<extra></extra>`;
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
    x: good.map((p) => p.metrics!.volatility),
    y: good.map((p) => p.metrics!.expected_return),
    marker: { color, size: line ? 4 : 12 },
    line: { color, width: 2.5 },
    customdata: good as never,
    hovertemplate: good.map((p) => hover(p, label, instruments)),
  };
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
          Доходность<b>{percent(p.metrics?.expected_return)}</b>
        </span>
        <span>
          Волатильность<b>{percent(p.metrics?.volatility)}</b>
        </span>
        <span>
          Sharpe<b>{number(p.metrics?.sharpe_ratio)}</b>
        </span>
      </div>
      <div className="allocations">
        {Object.entries(p.weights ?? {}).map(([a, w]) => (
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
          ];
        });
    const rows = [
      { label: "GMV", portfolio: result.gmv },
      { label: "Max Sharpe", portfolio: result.max_sharpe },
      { label: "Equal Weight", portfolio: result.equal_weight },
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
        ),
      ),
    );
    return d;
  }, [calculation, related, compare, instruments, rows]);
  const click = (e: PlotMouseEvent) => {
    const p = e.points[0];
    if (p?.customdata)
      setSelected({
        label: p.data.name ?? "Frontier point",
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
          ограничениями. Цвет соответствует методу; маркеры — GMV и Max Sharpe.
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
        Последовательные K-фолды без перемешивания. Обучение на K−1 фолдах,
        проверка на оставшемся; веса фиксированы. Это classical K-Fold, не
        walk-forward.
      </p>
      <Plot
        title="Веса CV-фолдов"
        data={data}
        layout={{
          barmode: "stack",
          yaxis: { tickformat: ".0%", title: { text: "Вес" } },
        }}
      />
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Fold</th>
              <th>Обучение</th>
              <th>Проверка</th>
              <th>Веса</th>
              <th>Train return / vol</th>
              <th>Validation return / vol</th>
              <th>Validation Sharpe</th>
            </tr>
          </thead>
          <tbody>
            {r.folds.map((f) => (
              <tr key={f.fold}>
                <td>
                  {f.fold}
                  <small>{f.status}</small>
                </td>
                <td>
                  {f.training.observations} мес.
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
                  {Object.entries(f.weights ?? {}).map(([a, w]) => (
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
            className={objective === key ? "active" : ""}
            onClick={() => setObjective(key)}
          >
            {key === "gmv" ? "GMV" : "Max Sharpe"}
          </button>
        ))}
      </div>
      <p className={o.status === "complete" ? "hint" : "warning"}>
        Статус {o.status} · {o.successful_iterations} / {o.requested_iterations}{" "}
        успешных · ошибок {o.failed_iterations}
      </p>
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
      <Plot
        title="Bootstrap веса и P5–P95"
        data={data}
        layout={{
          barmode: "group",
          yaxis: { tickformat: ".0%", title: { text: "Вес" } },
        }}
      />
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
      {o.stability && (
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
      <Plot
        title="Распределение bootstrap весов"
        data={distributions}
        layout={{
          barmode: "overlay",
          xaxis: { tickformat: ".0%", title: { text: "Вес" } },
          yaxis: { title: { text: "Количество итераций" } },
        }}
      />
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
              label: "Resampled point",
              portfolio: e.points[0].customdata as unknown as Portfolio,
            });
        }}
      />
      <PointDetail selected={selected} instruments={instruments} />
      <p className="hint">
        Усреднены веса при одинаковой risk aversion. Это не точная классическая
        efficient frontier.
      </p>
      {r.failed_iterations > 0 && (
        <pre>{JSON.stringify(r.failure_reasons, null, 2)}</pre>
      )}
    </>
  );
}
