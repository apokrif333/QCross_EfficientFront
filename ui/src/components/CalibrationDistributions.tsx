import { useEffect, useState } from "react";
import type { Data, Layout } from "plotly.js";
import { fetchCalibrationDetail } from "../lib/api";
import {
  horizonLabel,
  metricLabel,
  scoreNumber,
  scorePercent,
  type CalibrationDetail,
  type CalibrationResult,
} from "../lib/calibration";
import Plot from "./Plot";

const COLORS = [
  "#287586",
  "#a4493f",
  "#5e68a0",
  "#508347",
  "#986933",
  "#7e5482",
];
const families: Record<string, string> = {
  returns: "ACF Returns",
  absolute: "ACF Absolute Returns",
  squared: "ACF Squared Returns",
  sign: "ACF Signs",
};

function finite(values: (number | null)[]) {
  return values.filter((value): value is number => value !== null);
}

function mean(values: (number | null)[]) {
  const valid = finite(values);
  return valid.length
    ? valid.reduce((sum, value) => sum + value, 0) / valid.length
    : null;
}

function acfTraces(
  detail: CalibrationDetail,
  metrics: string[],
  lengths: number[],
  interval: string,
): Data[] {
  const x = metrics.map((metric) => Number(metric.split("_").at(-1)));
  const values = metrics.map((metric) => detail.metrics[metric]);
  const traces: Data[] = [
    {
      type: "scatter",
      mode: "lines+markers",
      x,
      y: values.map((v) => mean(v.train)),
      name: "Historical Train",
      line: { color: "#6e7a80", dash: "dot" },
    },
    {
      type: "scatter",
      mode: "lines+markers",
      x,
      y: values.map((v) =>
        interval === "all" ? mean(v.actual) : v.actual[Number(interval)],
      ),
      name: "Real Test",
      line: { color: "#183b36", width: 3 },
    },
  ];
  lengths.forEach((length, i) => {
    const color = COLORS[i % COLORS.length];
    const legendgroup = String(length);
    traces.push(
      {
        type: "scatter",
        mode: "lines",
        x,
        y: values.map((v) => v.series[length]?.p05),
        showlegend: false,
        legendgroup,
        line: { width: 0 },
        hoverinfo: "skip",
      },
      {
        type: "scatter",
        mode: "lines",
        x,
        y: values.map((v) => v.series[length]?.p95),
        showlegend: false,
        legendgroup,
        line: { width: 0 },
        fill: "tonexty",
        fillcolor: color + "22",
        hoverinfo: "skip",
      },
      {
        type: "scatter",
        mode: "lines+markers",
        x,
        y: values.map((v) => v.series[length]?.median),
        name: "L=" + length,
        legendgroup,
        line: { color },
      },
    );
  });
  return traces;
}

function histogramPlot(
  details: CalibrationDetail["metrics"][string],
  lengths: number[],
  interval: string,
  metric: string,
) {
  const actual =
    interval === "all"
      ? finite(details.actual)
      : finite([details.actual[Number(interval)] ?? null]);
  const data: Data[] = [];
  const shapes: Layout["shapes"] = [];
  const annotations: Layout["annotations"] = [];
  const selected = lengths
    .map((length) => details.series[length])
    .filter(Boolean);
  const extent =
    Math.max(...selected.map((v) => v.max), ...actual) -
    Math.min(...selected.map((v) => v.min), ...actual);
  const spike = Math.max(extent / 32, 1e-12);
  lengths.forEach((length, index) => {
    const series = details.series[length];
    if (!series) return;
    const color = COLORS[index % COLORS.length];
    const width = (series.max - series.min) / series.histogram.length || spike;
    const total = series.histogram.reduce((sum, count) => sum + count, 0);
    const constant = series.min === series.max;
    data.push({
      type: "bar",
      orientation: "v",
      name: "L=" + length,
      marker: { color },
      opacity: 0.52,
      width,
      x: series.histogram.map((_, i) =>
        constant ? series.min + i * width : series.min + (i + 0.5) * width,
      ),
      y: series.histogram.map((count) => count / (total * width)),
    });
  });
  const step = Math.min(0.05, 0.7 / Math.max(1, selected.length));
  lengths.forEach((length, index) => {
    const series = details.series[length];
    if (!series) return;
    const color = COLORS[index % COLORS.length];
    const y = 0.99 - index * step;
    shapes.push(
      {
        type: "line",
        x0: series.p05,
        x1: series.p95,
        y0: y,
        y1: y,
        yref: "paper",
        line: { color, width: 2 },
      },
      {
        type: "line",
        x0: series.median,
        x1: series.median,
        y0: y - step / 3,
        y1: y + step / 3,
        yref: "paper",
        line: { color, width: 3 },
      },
    );
  });
  actual.forEach((value) =>
    shapes.push({
      type: "line",
      x0: value,
      x1: value,
      y0: 0,
      y1: 1,
      yref: "paper",
      line: {
        width: actual.length === 1 ? 2 : 1,
        color: "#183b36",
        dash: "dot",
      },
      opacity: actual.length === 1 ? 1 : 0.45,
    }),
  );
  if (actual.length === 1)
    annotations.push({
      text: "Real Test",
      x: actual[0],
      y: 1.04,
      yref: "paper",
      showarrow: false,
    });
  const train = finite(details.train);
  if (train.length)
    data.push({
      type: "histogram",
      x: train,
      name: "Historical Train",
      histnorm: "probability density",
      marker: { color: "#6e7a80" },
      opacity: 0.35,
    });
  const layout: Partial<Layout> = {
    barmode: "overlay",
    shapes,
    annotations,
    xaxis: { title: { text: metricLabel(metric) } },
    yaxis: { title: { text: "Плотность вероятности" }, rangemode: "tozero" },
  };
  return (
    <Plot
      title={"Bootstrap и Historical Train · " + metricLabel(metric)}
      data={data}
      layout={layout}
    />
  );
}

function DetailTable({
  metrics,
  detail,
  lengths,
  interval,
}: {
  metrics: string[];
  detail: CalibrationDetail;
  lengths: number[];
  interval: string;
}) {
  const [first, last] =
    interval === "all"
      ? [0, detail.intervals.length]
      : [Number(interval), Number(interval) + 1];
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {[
              "Характеристика",
              "L",
              "Real Test",
              "P5",
              "P25",
              "Медиана",
              "P75",
              "P95",
              "Среднее",
              "SD",
              "Percentile rank",
              "CRPS",
              "Normalized CRPS",
              "Вырожденные сценарии",
            ].map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metrics.flatMap((metric) => {
            const item = detail.metrics[metric];
            return lengths
              .filter((length) => !!item.series[length])
              .map((length) => {
                const series = item.series[length];
                const crps =
                  interval === "all" ? series.mean_crps : series.crps[first];
                return (
                  <tr key={metric + length}>
                    <th>{metricLabel(metric)}</th>
                    <td>{length}</td>
                    <td>{scoreNumber(mean(item.actual.slice(first, last)))}</td>
                    {[
                      series.p05,
                      series.p25,
                      series.median,
                      series.p75,
                      series.p95,
                      series.mean,
                      series.std,
                    ].map((value, i) => (
                      <td key={i}>{scoreNumber(value)}</td>
                    ))}
                    <td>
                      {scorePercent(mean(series.ranks.slice(first, last)))}
                    </td>
                    <td>{scoreNumber(crps)}</td>
                    <td>
                      {scoreNumber(crps == null ? null : crps / item.scale)}
                    </td>
                    <td>{series.undefined}</td>
                  </tr>
                );
              });
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function CalibrationDistributions({
  result,
  jobId,
  demoDetail,
  selectedLength,
}: {
  result: CalibrationResult;
  jobId?: string;
  demoDetail?: CalibrationDetail;
  selectedLength: number;
}) {
  const [open, setOpen] = useState(false);
  const [foldChoice, setFold] = useState("");
  const [horizonChoice, setHorizon] = useState("12");
  const [metricChoice, setMetric] = useState("compounded_return");
  const [memberChoice, setMember] = useState("");
  const [interval, setInterval] = useState("0");
  const [explicitLengths, setLengths] = useState<number[]>();
  const [loaded, setLoaded] = useState<{
    selection: string;
    detail: CalibrationDetail;
  }>();
  const [error, setError] = useState("");
  const folds = Object.keys(result.runs);
  const fold = folds.includes(foldChoice) ? foldChoice : folds[0];
  const horizons = Object.keys(result.runs[fold] ?? {});
  const horizon = horizons.includes(horizonChoice)
    ? horizonChoice
    : horizons[0];
  const availability = result.runs[fold]?.[horizon] ?? {};
  const metrics = Object.keys(availability);
  const bulk: Record<string, { label: string; metrics: string[] }> = {};
  for (const [key, label] of Object.entries(families)) {
    const lags = metrics.filter((metric) =>
      metric.startsWith("acf_" + key + "_"),
    );
    if (lags.length) bulk["acf_" + key] = { label, metrics: lags };
  }
  const metric =
    metrics.includes(metricChoice) || bulk[metricChoice]
      ? metricChoice
      : metrics[0];
  const selectedMetrics = bulk[metric]?.metrics ?? [metric];
  const isAcf = !!bulk[metric];
  const members = availability[selectedMetrics[0]] ?? [];
  const memberKeys = members.map((member) => member.join("|"));
  const member = memberKeys.includes(memberChoice)
    ? memberChoice
    : memberKeys[0];
  const allLengths =
    result.demo_detail_lengths ??
    result.ranking.map((row) => row.length).sort((a, b) => a - b);
  const defaultLengths = allLengths.filter((length) =>
    [3, 6, 12, 24, selectedLength].includes(length),
  );
  const lengths =
    explicitLengths ??
    (defaultLengths.length ? defaultLengths : allLengths.slice(0, 4));
  const selection = JSON.stringify({
    fold,
    horizon,
    selectedMetrics,
    member,
    lengths,
  });
  const detail =
    demoDetail ?? (loaded?.selection === selection ? loaded.detail : undefined);
  const tickers = new Map(
    result.preview.assets.map((asset) => [
      String(asset.instrument_id),
      asset.ticker,
    ]),
  );
  useEffect(() => {
    if (!open || !jobId) return;
    const selected = JSON.parse(selection) as {
      fold: string;
      horizon: string;
      selectedMetrics: string[];
      member?: string;
      lengths: number[];
    };
    if (!selected.member || !selected.lengths.length) return;
    const controller = new AbortController();
    void fetchCalibrationDetail(
      jobId,
      selected.fold,
      selected.horizon,
      selected.selectedMetrics,
      selected.member.split("|"),
      selected.lengths,
      controller.signal,
    )
      .then((detail) => {
        setLoaded({ detail, selection });
        setError("");
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted) setError(err.message);
      });
    return () => controller.abort();
  }, [jobId, open, selection]);
  const compared = selectedMetrics.filter((key) => detail?.metrics[key]);
  const selectedInterval =
    interval === "all" || Number(interval) < (detail?.intervals.length ?? 0)
      ? interval
      : "0";

  return (
    <details
      className="calibration-section"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>Распределения Bootstrap и реальный Test</summary>
      {open && (
        <>
          <div className="calibration-grid calibration-section">
            <label>
              Fold
              <select
                value={fold}
                onChange={(e) => {
                  setFold(e.target.value);
                  setInterval("0");
                }}
              >
                {folds.map((id) => (
                  <option key={id} value={id}>
                    Fold {id}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Горизонт распределения
              <select
                value={horizon}
                onChange={(e) => {
                  setHorizon(e.target.value);
                  setInterval("0");
                }}
              >
                {horizons.map((id) => (
                  <option key={id} value={id}>
                    {horizonLabel(id)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Характеристика
              <select
                value={metric}
                onChange={(e) => setMetric(e.target.value)}
              >
                {Object.entries(bulk).map(([id, obj]) => (
                  <option key={id} value={id}>
                    {obj.label} · все лаги
                  </option>
                ))}
                {metrics.map((id) => (
                  <option key={id} value={id}>
                    {metricLabel(id)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Актив или пара
              <select
                value={member}
                onChange={(e) => setMember(e.target.value)}
              >
                {members.map((ids) => (
                  <option key={ids.join("|")} value={ids.join("|")}>
                    {ids.map((id) => tickers.get(id) ?? id).join(" / ")}
                  </option>
                ))}
              </select>
            </label>
            {detail && (
              <label>
                Реальный интервал
                <select
                  value={selectedInterval}
                  onChange={(e) => setInterval(e.target.value)}
                >
                  {detail.intervals.map((period, i) => (
                    <option key={period.start} value={i}>
                      {period.start} — {period.end}
                    </option>
                  ))}
                  <option value="all">Все интервалы</option>
                </select>
              </label>
            )}
          </div>
          <fieldset className="calibration-checks">
            <legend>Длины блока на графике</legend>
            {allLengths.map((length) => (
              <label key={length}>
                <input
                  type="checkbox"
                  checked={lengths.includes(length)}
                  disabled={lengths.length === 1 && lengths.includes(length)}
                  onChange={(e) =>
                    setLengths(
                      e.target.checked
                        ? [...lengths, length].sort((a, b) => a - b)
                        : lengths.filter((item) => item !== length),
                    )
                  }
                />{" "}
                L={length}
              </label>
            ))}
          </fieldset>
          {error && (
            <p role="alert" className="warning">
              {error}
            </p>
          )}
          {!detail && !error && (
            <p role="status">Загружаем рассчитанные распределения.</p>
          )}
          {detail && compared.length > 0 && (
            <>
              {isAcf ? (
                <Plot
                  title={bulk[metric].label}
                  data={acfTraces(detail, compared, lengths, selectedInterval)}
                  layout={{
                    xaxis: { title: { text: "Лаг, мес." }, dtick: 1 },
                    yaxis: { title: { text: "Корреляция" }, range: [-1, 1] },
                  }}
                />
              ) : (
                histogramPlot(
                  detail.metrics[compared[0]],
                  lengths,
                  selectedInterval,
                  compared[0],
                )
              )}
              <p className="hint">
                {isAcf
                  ? "Линия Bootstrap показывает медиану. Полоса соответствует P5–P95."
                  : "Перекладина показывает P5–P95. Риска отмечает медиану, пунктир — Real Test."}
                {selectedInterval === "all"
                  ? " В таблице показаны средние по доступным интервалам."
                  : ""}
              </p>
              <DetailTable
                metrics={compared}
                detail={detail}
                lengths={lengths}
                interval={selectedInterval}
              />
              <p className="hint">
                Масштаб Train{" "}
                {compared
                  .map(
                    (id) =>
                      metricLabel(id) +
                      " " +
                      scoreNumber(detail.metrics[id].scale) +
                      " (" +
                      detail.metrics[id].scale_method +
                      ")",
                  )
                  .join(" · ")}
                .
              </p>
            </>
          )}
        </>
      )}
    </details>
  );
}
