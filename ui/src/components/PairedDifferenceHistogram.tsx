import { useState } from "react";
import type { Data } from "plotly.js";
import type { PortfolioComparison } from "../types";
import Plot from "./Plot";

export default function PairedDifferenceHistogram({
  value,
}: {
  value?: PortfolioComparison | null;
}) {
  const [metric, setMetric] = useState<
    "delta_cagr" | "delta_volatility" | "delta_max_drawdown"
  >("delta_cagr");
  if (!value)
    return (
      <p className="hint">
        Для распределений разниц повторите Bootstrap с вашим портфелем.
      </p>
    );
  const label = {
    delta_cagr: "Разница CAGR",
    delta_volatility: "Разница волатильности",
    delta_max_drawdown: "Разница просадки",
  }[metric];
  const passive = "comparison_mode" in value;
  const samples = value.results.flatMap((r) => {
    const delta = r.differences?.[metric];
    return (passive || ("optimized" in r && r.optimized)) &&
      delta != null &&
      Number.isFinite(delta)
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
      <h3>
        {passive
          ? "Распределение разниц между активным и пассивным портфелями"
          : "Распределение разниц на Validation"}
      </h3>
      <p className="hint">
        {passive ? (
          "Пассивный портфель − активный, п.п. Для обеих версий используются одни и те же генерации Bootstrap."
        ) : (
          <>
            Оптимизированный портфель − ваш портфель, п.п. Для каждой пары
            используются одни и те же месяцы Validation.
          </>
        )}{" "}
        Успешных значений: {samples.length} из {value.requested_comparisons}.
        Горизонт: {value.horizon_months ?? "—"} мес. Для волатильности и
        максимальной просадки отрицательная разница означает снижение.
      </p>
      <div
        className="segmented"
        role="group"
        aria-label="Показатель гистограммы"
      >
        <button
          type="button"
          className={metric === "delta_cagr" ? "active" : ""}
          aria-pressed={metric === "delta_cagr"}
          onClick={() => setMetric("delta_cagr")}
        >
          Разница CAGR
        </button>
        <button
          type="button"
          className={metric === "delta_volatility" ? "active" : ""}
          aria-pressed={metric === "delta_volatility"}
          onClick={() => setMetric("delta_volatility")}
        >
          Разница волатильности
        </button>
        <button
          type="button"
          className={metric === "delta_max_drawdown" ? "active" : ""}
          aria-pressed={metric === "delta_max_drawdown"}
          onClick={() => setMetric("delta_max_drawdown")}
        >
          Разница просадки
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
