import { useMemo } from "react";
import type { Data } from "plotly.js";
import type { Instrument, PairedComparison, RiskReturn } from "../types";
import { percent, number, ticker } from "../lib/display";
import Plot from "./Plot";

const labels: Record<string, string> = {
  delta_arithmetic_return: "Δ годовой арифметической доходности",
  delta_cagr: "Δ CAGR",
  delta_volatility: "Δ годовой волатильности",
  delta_sharpe: "Δ Historical Sharpe",
  delta_max_drawdown: "Δ максимальной просадки",
};
const wins: Record<string, string> = {
  higher_cagr: "Выше CAGR",
  higher_sharpe: "Выше Sharpe",
  lower_volatility: "Ниже волатильность",
  higher_cagr_no_higher_volatility: "Выше CAGR при не большей волатильности",
};
export default function PairedComparisonView({
  value,
  instruments,
}: {
  value?: PairedComparison | null;
  instruments: Instrument[];
}) {
  const data = useMemo(() => {
    if (!value?.clouds?.length) return [];
    const clouds = value.clouds;
    const density = (
      name: string,
      points: RiskReturn[],
      color: string,
      asset = false,
    ): Data =>
      ({
        type: "histogram2dcontour",
        name: `${name} (${points.length})`,
        x: points.map((p) => p.volatility),
        y: points.map((p) => p.expected_return),
        histnorm: "probability density",
        nbinsx: 25,
        nbinsy: 25,
        ncontours: 8,
        contours: { coloring: "fill", showlines: false },
        colorscale: [
          [0, "rgba(255,255,255,0)"],
          [1, color],
        ],
        showscale: false,
        showlegend: true,
        opacity: 0.65,
        visible: asset ? "legendonly" : true,
        hovertemplate: `<b>${name}</b><br>Волатильность: %{x:.2%}<br>Годовая арифметическая доходность: %{y:.2%}<br>Плотность: %{z:.3g}<extra></extra>`,
      }) as Data;
    return [
      ...Object.keys(clouds[0].assets).map((a, i) =>
        density(
          ticker(a, instruments),
          clouds.map((c) => c.assets[a]),
          ["#b98943", "#4575b4", "#8c6bb1", "#66a61e"][i % 4],
          true,
        ),
      ),
      density(
        "Ваш портфель",
        clouds.map((c) => c.user),
        "#197b83",
      ),
      density(
        "Оптимизированный на Train",
        clouds.flatMap((c) => (c.optimized ? [c.optimized] : [])),
        "#b34b71",
      ),
      {
        type: "scatter",
        mode: "lines",
        name: "Ex-post Validation Efficient Frontier",
        x: value.validation_frontier?.map((p) => p.volatility),
        y: value.validation_frontier?.map((p) => p.expected_return),
        line: { color: "#172c38", width: 2 },
        hovertemplate:
          "Ex-post граница<br>Волатильность: %{x:.2%}<br>Доходность: %{y:.2%}<extra></extra>",
      } as Data,
    ];
  }, [value, instruments]);
  if (!value) return null;
  const format = (key: string, v: number | null | undefined) =>
    key === "delta_sharpe" ? number(v) : percent(v);
  return (
    <section className="subsection">
      <h3>Ваш портфель и оптимизатор — проверка на Validation</h3>
      {value.user_weights && (
        <p className="hint">
          {value.user_portfolio_mode === "controlled"
            ? "Исходные целевые веса управляемого портфеля:"
            : "Ваш фиксированный портфель:"}{" "}
          {Object.entries(value.user_weights)
            .filter(([, w]) => w > 0)
            .map(([a, w]) => `${ticker(a, instruments)} ${percent(w)}`)
            .join(" · ")}
        </p>
      )}
      <p className="hint">
        {value.user_portfolio_mode === "controlled" && (
          <>
            Сравнение с портфелем под управлением. Лимит риска оптимизатора —
            фактическая годовая волатильность управляемой траектории на Train
            после комиссий. Сигналы на Validation рассчитываются последовательно
            заново.{" "}
          </>
        )}
        Веса оптимизатора выбраны только на Train при риске вашего портфеля на
        Train. Δ = оптимизатор − ваш портфель. Для волатильности и просадки
        уменьшение — улучшение. Реализованный риск на Validation может
        отличаться.
      </p>
      <p>
        {value.generated_datasets != null
          ? `${value.generated_datasets} генераций · ${value.requested_comparisons} пар · горизонт каждой генерации ${value.horizon_months} мес. · `
          : ""}
        Успешных сравнений: {value.valid_count} / {value.requested_comparisons};
        ошибок: {value.failed_count}.
      </p>
      <div className="metrics">
        {Object.entries(value.win_frequencies).map(([k, v]) => (
          <span key={k}>
            {wins[k] ?? k}
            <b>{percent(v.fraction)}</b>
            <small>{v.denominator} сравнений</small>
          </span>
        ))}
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Разница на Validation</th>
              <th>Mean</th>
              <th>Median</th>
              <th>P5–P95</th>
              <th>Min / Max</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(value.differences).map(([k, v]) => (
              <tr key={k}>
                <td>{labels[k] ?? k}</td>
                <td>{format(k, v?.mean)}</td>
                <td>{format(k, v?.median)}</td>
                <td>
                  {format(k, v?.p5)} — {format(k, v?.p95)}
                </td>
                <td>
                  {format(k, v?.minimum)} / {format(k, v?.maximum)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.length > 0 && (
        <>
          <h4>Validation: распределение доходности и риска</h4>
          <p className="hint">
            Более плотные области темнее. Активы включаются нажатием на легенду.
            Оба набора синтетических данных получены из одной исторической
            выборки. Частоты и процентили не являются вероятностями будущих
            результатов. Граница построена ретроспективно по средним
            эмпирическим моментам Validation.
          </p>
          <Plot
            title="Validation density clouds"
            data={data}
            layout={{
              xaxis: {
                title: { text: "Годовая волатильность" },
                tickformat: ".1%",
              },
              yaxis: {
                title: { text: "Годовая арифметическая доходность" },
                tickformat: ".1%",
              },
            }}
          />
        </>
      )}
    </section>
  );
}
