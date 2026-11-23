import { useMemo, useState } from "react";
import type { Data, Layout, PlotRestyleEvent } from "plotly.js";
import { ticker } from "../lib/display";
import type {
  BootstrapTrendSensitivity as Sensitivity,
  BootstrapTrendSeries,
  Instrument,
} from "../types";
import Plot from "./Plot";
import BootstrapTrendComparison from "./BootstrapTrendComparison";

const TITLE = "Чувствительность к периоду SMA/Momentum";
const BINS = 25;
const DEFAULT_PERIOD = 12;
const RISK_LABELS = {
  1: "Максимальная просадка",
  2: "Годовая волатильность",
} as const;
type Risk = keyof typeof RISK_LABELS;

function axisBins(min: number, max: number) {
  const padding =
    max === min ? Math.max(Math.abs(min) * 0.05, 0.01) : (max - min) / BINS;
  const lower = min - padding;
  const upper = max + padding;
  const step = (upper - lower) / BINS;
  return {
    range: [lower, upper],
    centers: Array.from({ length: BINS }, (_, i) => lower + (i + 0.5) * step),
    index: (v: number) => Math.floor((v - lower) / step),
    step,
  };
}

function densityPlot(series: BootstrapTrendSeries, risk: Risk, gross: boolean) {
  const offset = gross ? 3 : 0;
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity,
    missing = 0;
  for (const samples of series) {
    for (const point of samples) {
      const x = point[offset + risk],
        y = point[offset];
      if (x == null || y == null) {
        missing++;
        continue;
      }
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    }
  }
  if (minX === Infinity) return null;
  const x = axisBins(minX, maxX);
  const y = axisBins(minY, maxY);
  let peak = 0,
    minimum = Infinity;
  const densities = series.map((samples) => {
    const z = Array.from({ length: BINS }, () => Array<number>(BINS).fill(0));
    for (const point of samples) {
      const a = point[offset + risk],
        b = point[offset];
      if (a != null && b != null) z[y.index(b)][x.index(a)]++;
    }
    const binArea = x.step * y.step;
    return z.map((row) =>
      row.map((count) => {
        const density = count / (samples.length * binArea);
        peak = Math.max(peak, density);
        if (count) minimum = Math.min(minimum, density);
        return density;
      }),
    );
  });
  const data = densities.map((z, i) => {
    const period = i + 1;
    const name = `${period} мес.`;
    const hue = (i * 137.508 + 195) % 360;
    return {
      type: "contour",
      uid: String(period),
      name,
      x: x.centers,
      y: y.centers,
      z,
      zmin: 0,
      zmax: peak,
      autocontour: false,
      contours: {
        start: minimum / 2,
        end: peak,
        size: peak / 8,
        coloring: "fill",
        showlines: false,
      },
      colorscale: [
        [0, `hsla(${hue}, 68%, 41%, 0)`],
        [1, `hsla(${hue}, 68%, 41%, 0.85)`],
      ],
      showscale: false,
      showlegend: true,
      hovertemplate: `<b>${name}</b><br>${RISK_LABELS[risk]}: %{x:.2%}<br>CAGR: %{y:.2%}<br>Плотность: %{z:.3g}<extra></extra>`,
    };
  });
  const layout: Partial<Layout> = {
    xaxis: {
      range: x.range,
      tickformat: ".1%",
      title: { text: RISK_LABELS[risk] },
    },
    yaxis: { range: y.range, tickformat: ".1%", title: { text: "CAGR" } },
    margin: { l: 66, r: 24, t: 20, b: 148 },
    legend: { orientation: "h", x: 0, y: -0.19 },
  };
  return { data, layout, missing };
}

export default function BootstrapTrendSensitivity({
  value,
  instruments,
  busy,
  onCalculate,
}: {
  value?: Sensitivity | null;
  instruments: Instrument[];
  busy?: boolean;
  onCalculate?: () => void;
}) {
  const [asset, setAsset] = useState("");
  const [risk, setRisk] = useState<Risk>(1);
  const [selectedGross, setGross] = useState(false);
  const [benchmark, setBenchmark] = useState(DEFAULT_PERIOD);
  const [visible, setVisible] = useState(() => new Set([DEFAULT_PERIOD]));
  const hasGross = value?.portfolio[0]?.[0]?.length === 7;
  const gross = hasGross && selectedGross;
  const scopes = ["", ...Object.keys(value?.by_asset ?? {})];
  const selectedAsset = value?.by_asset[asset] ? asset : "";
  const scope = scopes.indexOf(selectedAsset);
  const series = selectedAsset
    ? value?.by_asset[selectedAsset]
    : value?.portfolio;
  const plot = useMemo(
    () => (series ? densityPlot(series, risk, gross) : null),
    [series, risk, gross],
  );
  const data = useMemo(
    () =>
      plot?.data.map(
        (trace, i) =>
          ({
            ...trace,
            visible: visible.has(i + 1) ? true : "legendonly",
            contours: { ...trace.contours, showlines: benchmark === i + 1 },
            line: { color: "#142b38", width: 1.4 },
          }) as Data,
      ),
    [plot, visible, benchmark],
  );
  function selectPeriod(period: number) {
    setBenchmark(period);
    setVisible((current) =>
      current.has(period) ? current : new Set([...current, period]),
    );
  }
  function changeVisibility([update, indices]: PlotRestyleEvent) {
    const next: unknown = update.visible;
    if (!Array.isArray(next)) return;
    setVisible((current) => {
      const updated = new Set(current);
      indices.forEach((index, i) => {
        if (next[i] === true) updated.add(index + 1);
        else if (next[i] === "legendonly" || next[i] === false)
          updated.delete(index + 1);
      });
      return updated;
    });
  }
  if (!value && !onCalculate) return null;
  return (
    <section className="subsection">
      <h3>{TITLE}</h3>
      {!value && onCalculate && (
        <button type="button" disabled={busy} onClick={onCalculate}>
          Рассчитать чувствительность
        </button>
      )}
      {value && series && (
        <>
          <div className="sensitivity-actions">
            {scopes.length > 1 && (
              <button
                type="button"
                aria-label="Предыдущий график"
                onClick={() =>
                  setAsset(scopes[(scope - 1 + scopes.length) % scopes.length])
                }
              >
                ←
              </button>
            )}
            <strong aria-live="polite">
              {selectedAsset
                ? `${ticker(selectedAsset, instruments)} · 100%`
                : "Весь портфель"}
            </strong>
            {scopes.length > 1 && (
              <button
                type="button"
                aria-label="Следующий график"
                onClick={() => setAsset(scopes[(scope + 1) % scopes.length])}
              >
                →
              </button>
            )}
            <div className="segmented" role="group" aria-label="Учёт комиссий">
              <button
                type="button"
                aria-pressed={!gross}
                className={!gross ? "active" : undefined}
                onClick={() => setGross(false)}
              >
                Net
              </button>
              <button
                type="button"
                aria-pressed={gross}
                className={gross ? "active" : undefined}
                disabled={!hasGross}
                onClick={() => setGross(true)}
              >
                Gross
              </button>
            </div>
            <label>
              Риск{" "}
              <select
                value={risk}
                onChange={(event) =>
                  setRisk(event.target.value === "1" ? 1 : 2)
                }
              >
                <option value={1}>{RISK_LABELS[1]}</option>
                <option value={2}>{RISK_LABELS[2]}</option>
              </select>
            </label>
          </div>
          <p className="hint">
            {value.iterations} общих Bootstrap-генераций на каждый период.
            {!hasGross &&
              " В сохранённом расчёте доступны только показатели Net."}
          </p>
          {plot && data ? (
            <>
              {plot.missing > 0 && (
                <p className="warning">
                  Недоступных сочетаний доходности и риска: {plot.missing}.
                </p>
              )}
              <Plot
                key={selectedAsset}
                title={TITLE}
                data={data}
                layout={plot.layout}
                height={500}
                onRestyle={changeVisibility}
              />
            </>
          ) : (
            <p className="warning">Нет доступных оценок плотности.</p>
          )}
          <BootstrapTrendComparison
            series={series}
            benchmark={benchmark}
            gross={gross}
            iterations={value.iterations}
            commissionBasis={value.commission_basis}
            onSelect={selectPeriod}
          />
        </>
      )}
    </section>
  );
}
