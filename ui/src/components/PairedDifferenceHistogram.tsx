import { useState } from "react";
import type { Data } from "plotly.js";
import type { PairedComparison } from "../types";
import Plot from "./Plot";

export default function PairedDifferenceHistogram({
  value,
}: {
  value?: PairedComparison | null;
}) {
  const [metric, setMetric] = useState<"delta_cagr" | "delta_volatility">(
    "delta_cagr",
  );
  if (!value)
    return (
      <p className="hint">
        Для распределений разниц повторите Bootstrap с вашим портфелем.
      </p>
    );
  const isCagr = metric === "delta_cagr";
  const label = isCagr ? "Разница CAGR" : "Разница волатильности";
  const samples = value.results.flatMap((r) => {
    const delta = r.differences?.[metric];
    return r.optimized && delta != null && Number.isFinite(delta)
      ? [delta * 100]
      : [];
  });
  const trace = {
    type: "histogram",
    x: samples,
    nbinsx: 30,
    name: label,
    hovertemplate: `<b>${label}</b><br>Середина интервала: %{x} п.п.<br>Число пар: %{y}<extra></extra>`,
  } as Data;
  return (
    <section className="subsection">
      <h3>Распределение разниц на Validation</h3>
      <p className="hint">
        Оптимизированный портфель − ваш портфель, п.п. Для каждой пары
        используются одни и те же месяцы Validation. Успешных значений:{" "}
        {samples.length} из {value.requested_comparisons}. Горизонт:{" "}
        {value.horizon_months ?? "—"} мес. Для волатильности отрицательная
        разница означает снижение риска.
      </p>
      <div
        className="segmented"
        role="group"
        aria-label="Показатель гистограммы"
      >
        <button
          type="button"
          className={isCagr ? "active" : ""}
          aria-pressed={isCagr}
          onClick={() => setMetric("delta_cagr")}
        >
          Разница CAGR
        </button>
        <button
          type="button"
          className={!isCagr ? "active" : ""}
          aria-pressed={!isCagr}
          onClick={() => setMetric("delta_volatility")}
        >
          Разница волатильности
        </button>
      </div>
      <h4>{label}, п.п.</h4>
      {samples.length ? (
        <Plot
          title={`Bootstrap: ${label}`}
          data={[trace]}
          layout={{
            xaxis: { title: { text: `${label}, п.п.` } },
            yaxis: { title: { text: "Число пар" }, rangemode: "tozero" },
          }}
        />
      ) : (
        <p className="hint">Нет доступных пар для этого показателя.</p>
      )}
    </section>
  );
}
