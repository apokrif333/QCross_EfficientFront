import { useState } from "react";
import type { Data } from "plotly.js";
import Plot from "./Plot";

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

export default function PortfolioPathCharts({
  titleSuffix,
  months,
  paths,
}: {
  titleSuffix: string;
  months: string[];
  paths: { name: string; wealth: (number | null)[] }[];
}) {
  const [scale, setScale] = useState<"linear" | "log">("linear");
  const dates = [months[0], ...months];
  return (
    <>
      <div className="wealth-scale">
        <div className="segmented" role="group" aria-label="Шкала капитала">
          <button
            type="button"
            className={scale === "linear" ? "active" : ""}
            aria-pressed={scale === "linear"}
            onClick={() => setScale("linear")}
          >
            Обычная
          </button>
          <button
            type="button"
            className={scale === "log" ? "active" : ""}
            aria-pressed={scale === "log"}
            onClick={() => setScale("log")}
          >
            Логарифмическая
          </button>
        </div>
      </div>
      <Plot
        title={`Капитал: ${titleSuffix}`}
        data={paths.map(({ name, wealth }): Data => ({
          type: "scatter",
          mode: "lines",
          name,
          x: dates,
          y:
            scale === "log"
              ? wealth.map((value) => (value === 0 ? null : value))
              : wealth,
        }))}
        layout={{ yaxis: { type: scale } }}
      />
      <Plot
        title={`Просадки: ${titleSuffix}`}
        data={paths.map(({ name, wealth }): Data => ({
          type: "scatter",
          mode: "lines",
          name,
          x: dates,
          y: drawdowns(wealth),
          hovertemplate: "%{fullData.name}<br>%{x}: %{y:.2%}<extra></extra>",
        }))}
        layout={{ yaxis: { title: { text: "Просадка" }, tickformat: ".0%" } }}
      />
    </>
  );
}
