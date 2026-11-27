import { useMemo } from "react";
import type { Data } from "plotly.js";
import type { Instrument, PortfolioComparison, RiskReturn } from "../types";
import { percent, number, ticker } from "../lib/display";
import Plot from "./Plot";
import BootstrapValidationTable from "./BootstrapValidationTable";

const labels: Record<string, string> = {
  delta_cagr: "Δ CAGR",
  delta_volatility: "Δ годовой волатильности",
  delta_sharpe: "Δ Historical Sharpe",
  delta_max_drawdown: "Δ максимальной просадки",
  delta_calmar: "Δ Calmar",
  delta_sortino: "Δ Sortino",
  delta_terminal_wealth: "Δ конечного капитала на $1",
  delta_annualized_commissions: "Δ годовых комиссий",
  delta_annualized_capital_gains_tax: "Δ годового налога",
  delta_annualized_total_costs: "Δ совокупных годовых издержек",
  delta_turnover: "Δ годового оборота",
};
const hiddenDifferences = new Set([
  "delta_arithmetic_return",
  "delta_commissions",
  "delta_capital_gains_tax",
  "delta_total_costs",
]);
const wins: Record<string, string> = {
  higher_cagr: "Выше CAGR",
  higher_sharpe: "Выше Sharpe",
  lower_volatility: "Ниже волатильность",
  lower_drawdown: "Ниже просадки",
  higher_cagr_no_higher_drawdown: "Выше CAGR при не большей просадке",
  higher_cagr_no_higher_volatility: "Выше CAGR при не большей волатильности",
};
export default function PairedComparisonView({
  value,
  instruments,
  bootstrap = false,
}: {
  value?: PortfolioComparison | null;
  instruments: Instrument[];
  bootstrap?: boolean;
}) {
  const data = useMemo(() => {
    if (!value?.clouds?.length) return [];
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
    if ("comparison_mode" in value) {
      return [
        density(
          "Ваш портфель под управлением",
          value.clouds.map((c) => c.active),
          "#197b83",
        ),
        density(
          "Пассивный портфель",
          value.clouds.map((c) => c.passive),
          "#b34b71",
        ),
      ];
    }
    const clouds = value.clouds;
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
        value.user_portfolio_mode === "controlled"
          ? "Ваш портфель под управлением"
          : "Ваш портфель",
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
  const passive = "comparison_mode" in value;
  const joined =
    !passive &&
    value.analysis_version === "bidirectional_block_cv_stationary_bootstrap_v1";
  const frequencies = { ...value.win_frequencies };
  delete frequencies.lower_annualized_total_costs;
  // Older snapshots store row-level deltas but may lack these summaries.
  if (
    !frequencies.higher_cagr_no_higher_drawdown ||
    !frequencies.lower_drawdown
  ) {
    const comparisons = passive
      ? value.results
      : value.results.filter((row) => row.optimized != null);
    let drawdowns = 0,
      lowerDrawdowns = 0,
      jointPairs = 0,
      jointWins = 0;
    for (const row of comparisons) {
      const drawdown = row.differences?.delta_max_drawdown;
      if (drawdown == null || !Number.isFinite(drawdown)) continue;
      drawdowns++;
      if (drawdown < 0) lowerDrawdowns++;
      const cagr = row.differences?.delta_cagr;
      if (cagr == null || !Number.isFinite(cagr)) continue;
      jointPairs++;
      if (cagr > 0 && drawdown <= 0) jointWins++;
    }
    if (!passive && value.results.length) {
      frequencies.lower_drawdown ??= {
        fraction: drawdowns ? lowerDrawdowns / drawdowns : null,
        denominator: drawdowns,
      };
    }
    frequencies.higher_cagr_no_higher_drawdown ??= {
      fraction: jointPairs ? jointWins / jointPairs : null,
      denominator: jointPairs,
    };
  }
  const format = (key: string, v: number | null | undefined) =>
    [
      "delta_sharpe",
      "delta_sortino",
      "delta_calmar",
      "delta_terminal_wealth",
    ].includes(key)
      ? number(v)
      : percent(v);
  return (
    <section className="subsection">
      <h3>
        {passive
          ? "Ваш активный и пассивный портфели — Stationary Bootstrap"
          : "Ваш портфель и оптимизатор — проверка на Validation"}
      </h3>
      {value.user_weights && (
        <p className="hint">
          {passive
            ? "Исходные целевые веса обеих версий:"
            : value.user_portfolio_mode === "controlled"
              ? "Исходные целевые веса управляемого портфеля:"
              : "Ваш фиксированный портфель:"}{" "}
          {Object.entries(value.user_weights)
            .filter(([, w]) => w > 0)
            .map(([a, w]) => `${ticker(a, instruments)} ${percent(w)}`)
            .join(" · ")}
        </p>
      )}
      <p className="hint">
        {passive ? (
          <>
            На каждой генерации обе версии используют те же месяцы, ставку
            комиссии и режим ребалансировки. Пассивная версия использует
            исходные целевые веса. Δ = пассивный − активный, после комиссий. Для
            волатильности и просадки уменьшение — улучшение.
          </>
        ) : (
          <>
            {value.user_portfolio_mode === "controlled" && (
              <>
                Сравнение с портфелем под управлением. Лимит риска оптимизатора
                — фактическая годовая волатильность управляемой траектории на
                Train после комиссий. Сигналы на Validation рассчитываются
                последовательно заново.{" "}
              </>
            )}
            {joined &&
              "Каждая пара использует одну объединённую Validation. Веса оптимизатора меняются на границах Fold. "}
            Веса оптимизатора выбраны только на Train при риске вашего портфеля
            на Train. Δ = оптимизатор − ваш портфель. Для волатильности и
            просадки уменьшение — улучшение. Реализованный риск на Validation
            может отличаться.
          </>
        )}
      </p>
      <p className="hint">
        Для комиссий и налога отрицательная разница означает преимущество
        первого портфеля в формуле Δ. Издержки относятся к Net.
      </p>
      <p>
        {value.generated_datasets != null
          ? `${value.generated_datasets} генераций · ${value.requested_comparisons} пар · горизонт каждой генерации ${value.horizon_months} мес. · `
          : ""}
        Успешных сравнений: {value.valid_count} / {value.requested_comparisons};
        ошибок: {value.failed_count}.
      </p>
      <div className="metrics">
        {Object.entries(frequencies).map(([k, v]) => (
          <span key={k}>
            {passive && "Пассивный · "}
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
              <th>
                {passive
                  ? "Разница: пассивный − активный"
                  : "Разница на Validation"}
              </th>
              <th>Mean</th>
              <th>Median</th>
              {(joined || passive) && <th>SD</th>}
              <th>P5–P95</th>
              <th>Min / Max</th>
              {(joined || passive) && <th>Доступно</th>}
            </tr>
          </thead>
          <tbody>
            {Object.entries(value.differences)
              .filter(
                ([key]) =>
                  !hiddenDifferences.has(key) &&
                  (!(bootstrap || passive) || key !== "delta_terminal_wealth"),
              )
              .map(([k, v]) => (
                <tr key={k}>
                  <td>{labels[k] ?? k}</td>
                  <td>{format(k, v?.mean)}</td>
                  <td>{format(k, v?.median)}</td>
                  {(joined || passive) && (
                    <td>{format(k, v?.standard_deviation)}</td>
                  )}
                  <td>
                    {format(k, v?.p5)} — {format(k, v?.p95)}
                  </td>
                  <td>
                    {format(k, v?.minimum)} / {format(k, v?.maximum)}
                  </td>
                  {(joined || passive) && <td>{v?.valid_count ?? 0}</td>}
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {!passive &&
        value.generated_datasets != null &&
        value.results.length > 0 && (
          <BootstrapValidationTable value={value} instruments={instruments} />
        )}
      {data.length > 0 && (
        <>
          <h4>
            {passive
              ? "Активный и пассивный портфели: распределение доходности и риска"
              : "Validation: распределение доходности и риска"}
          </h4>
          <p className="hint">
            Более плотные области темнее.{" "}
            {passive ? (
              "Каждое облако построено по одним и тем же генерациям Bootstrap."
            ) : (
              <>
                Активы включаются нажатием на легенду.{" "}
                {joined
                  ? "Train и Validation ресэмплируются из непересекающихся исторических интервалов внутри Fold."
                  : "Оба набора синтетических данных получены из одной исторической выборки."}{" "}
                Частоты и процентили не являются вероятностями будущих
                результатов. Граница построена ретроспективно по средним
                эмпирическим моментам Validation.
              </>
            )}
          </p>
          <Plot
            title={
              passive
                ? "Плотность доходности и риска активного и пассивного портфелей"
                : "Validation density clouds"
            }
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
