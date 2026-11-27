import { useState } from "react";
import type { Data } from "plotly.js";
import { downloadJson } from "../lib/snapshot";
import {
  bestLength,
  GROUP_LABELS,
  horizonLabel,
  metricLabel,
  SCORE_LABELS,
  scoreNumber,
  scorePercent,
  type CalibrationDetail,
  type CalibrationResult,
  type CalibrationScore,
  type ScoreMatrix,
} from "../lib/calibration";
import CalibrationDistributions from "./CalibrationDistributions";
import Plot from "./Plot";

function ScoreTable({
  matrix,
  lengths,
  label,
}: {
  matrix: ScoreMatrix;
  lengths: number[];
  label: (id: string) => string;
}) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Метрика</th>
            {lengths.map((length) => (
              <th key={length}>L={length}</th>
            ))}
            <th>Best L</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(matrix).map(([key, row]) => {
            const best = bestLength(row);
            return (
              <tr key={key}>
                <th scope="row">{label(key)}</th>
                {lengths.map((length) => (
                  <td
                    className={
                      String(length) === best ? "calibration-best" : undefined
                    }
                    key={length}
                  >
                    {scoreNumber(row[length])}
                  </td>
                ))}
                <td>{best ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function CalibrationResults({
  result,
  jobId,
  demoDetail,
  onUseLength,
}: {
  result: CalibrationResult;
  jobId?: string;
  demoDetail?: CalibrationDetail;
  onUseLength?: (length: number) => void;
}) {
  const [metric, setMetric] = useState<CalibrationScore>("crps");
  const [showHorizons, setShowHorizons] = useState(false);
  const [length, setLength] = useState(result.optimal_length);
  const ranking = [...result.ranking].sort((a, b) => a.length - b.length);
  const lengths = ranking.map((row) => row.length);
  const baseline = ranking.find((row) => row.length === 12);
  const comparable = ranking.filter((row) => row.comparable);
  const winner = ranking.find((row) => row.length === result.optimal_length)!;
  const pit = result.pit[length];
  const aggregate = (row: typeof winner) =>
    metric === "crps" ? row.score : row[metric];
  const traces: Data[] = [
    {
      type: "scatter",
      mode: "lines+markers",
      x: lengths,
      y: ranking.map(aggregate),
      name: SCORE_LABELS[metric],
      marker: { color: "#32788d" },
      line: { color: "#32788d", width: 2.5 },
    },
  ];
  if (metric === "crps" && result.phase === "calibration") {
    traces.push(
      {
        type: "scatter",
        mode: "markers",
        x: comparable.map((row) => row.length),
        y: comparable.map((row) => row.score),
        name: "Практически сопоставимые L",
        marker: { color: "#419885", size: 10, symbol: "circle-open" },
      },
      {
        type: "scatter",
        mode: "markers",
        x: [winner.length],
        y: [winner.score],
        name: "Минимум",
        marker: { color: "#226956", size: 12, symbol: "diamond" },
      },
    );
  }
  if (showHorizons)
    for (const [horizon, scores] of Object.entries(result.by_horizon)) {
      const y =
        metric === "crps"
          ? lengths.map((l) => scores[l])
          : lengths.map((l) => {
              const rows = result.rows.filter(
                (row) => row.horizon === Number(horizon) && row.length === l,
              );
              return rows.length
                ? rows.reduce((total, row) => total + row[metric], 0) /
                    rows.length
                : null;
            });
      traces.push({
        type: "scatter",
        mode: "lines",
        x: lengths,
        y,
        name: horizonLabel(horizon),
        line: { width: 1.5 },
      });
    }
  const holdout = result.phase === "holdout";
  const horizonRows = Object.entries(result.by_horizon);
  const showBaseline =
    !holdout && lengths[0] <= 12 && lengths[lengths.length - 1] >= 12;
  const currentScore = result.ranking.find((row) => row.length === length);

  return (
    <div className="calibration-section">
      <dl className="calibration-summary">
        <div>
          <dt>
            {holdout ? "Выбранное L для Holdout" : "Оптимальная длина блока"}
          </dt>
          <dd>{result.optimal_length} мес.</dd>
        </div>
        <div>
          <dt>{holdout ? "Holdout CRPS" : "Минимальный CRPS"}</dt>
          <dd>{scoreNumber(result.minimum_score)}</dd>
        </div>
        {!holdout && (
          <>
            <div>
              <dt>CRPS при L=12</dt>
              <dd>
                {scoreNumber(baseline?.score)}{" "}
                <small>
                  {baseline
                    ? scorePercent(baseline.relative_difference) + " к минимуму"
                    : "вне сетки"}
                </small>
              </dd>
            </div>
            <div>
              <dt>Три лучших L</dt>
              <dd>
                {result.ranking
                  .slice(0, 3)
                  .map((row) => row.length)
                  .join(" · ")}
              </dd>
            </div>
          </>
        )}
      </dl>
      {!holdout && (
        <p>
          Практически сопоставимые L, мес.{" "}
          {comparable.map((row) => row.length).join(", ")}.
        </p>
      )}
      <div className="calibration-actions">
        <label>
          {holdout ? "L в Holdout" : "Длина блока для анализа"}
          <select
            aria-label="Выбранная длина блока"
            value={length}
            onChange={(e) => setLength(Number(e.target.value))}
          >
            {lengths.map((item) => (
              <option key={item} value={item}>
                {item} мес.
              </option>
            ))}
          </select>
        </label>
        {onUseLength && (
          <button className="primary" onClick={() => onUseLength(length)}>
            Use Selected Block Length
          </button>
        )}
        <button
          onClick={() =>
            downloadJson(
              "bootstrap-calibration-" + result.phase + ".json",
              result,
            )
          }
        >
          Скачать результат JSON
        </button>
      </div>
      <div className="calibration-actions">
        <label>
          Критерий графика
          <select
            value={metric}
            onChange={(e) => setMetric(e.target.value as CalibrationScore)}
          >
            {Object.entries(SCORE_LABELS).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="calibration-inline">
          <input
            type="checkbox"
            checked={showHorizons}
            onChange={(e) => setShowHorizons(e.target.checked)}
          />{" "}
          Кривые по горизонтам
        </label>
      </div>
      <Plot
        title={SCORE_LABELS[metric]}
        data={traces}
        layout={{
          xaxis: { title: { text: "Expected Block Length, мес." }, dtick: 1 },
          yaxis: { title: { text: SCORE_LABELS[metric] }, rangemode: "tozero" },
          ...(showBaseline
            ? {
                shapes: [
                  {
                    type: "line" as const,
                    x0: 12,
                    x1: 12,
                    y0: 0,
                    y1: 1,
                    yref: "paper" as const,
                    line: { color: "#6f7b83", dash: "dot" as const, width: 1 },
                  },
                ],
                annotations: [
                  {
                    x: 12,
                    y: 1,
                    yref: "paper" as const,
                    text: "L=12",
                    showarrow: false,
                    xshift: 14,
                    yshift: 10,
                  },
                ],
              }
            : {}),
        }}
      />
      {!holdout && (
        <details className="calibration-section">
          <summary>Рейтинг длин блока</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Место</th>
                  <th>L, мес.</th>
                  <th>Normalized CRPS</th>
                  <th>К минимуму</th>
                  {Object.values(SCORE_LABELS)
                    .slice(1)
                    .map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {result.ranking.map((row, rank) => (
                  <tr key={row.length}>
                    <td>{rank + 1}</td>
                    <th>{row.length}</th>
                    <td>{scoreNumber(row.score)}</td>
                    <td>{scorePercent(row.relative_difference)}</td>
                    <td>{scoreNumber(row.energy)}</td>
                    <td>{scoreNumber(row.temporal_returns)}</td>
                    <td>{scoreNumber(row.temporal_absolute)}</td>
                    <td>{scoreNumber(row.cross_asset)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <details open className="calibration-section">
        <summary>Результаты по горизонтам</summary>
        <ScoreTable
          matrix={{
            ...result.by_horizon,
            total: Object.fromEntries(
              ranking.map((row) => [row.length, row.score]),
            ),
          }}
          lengths={lengths}
          label={(id) => (id === "total" ? "Итоговый Score" : horizonLabel(id))}
        />
        <p className="hint">
          Вес каждого доступного горизонта{" "}
          {scorePercent(Object.values(result.horizon_weights)[0])}.
        </p>
        <Plot
          title="Тепловая карта Normalized CRPS"
          height={320}
          data={[
            {
              type: "heatmap",
              x: lengths,
              y: horizonRows.map(([key]) => horizonLabel(key)),
              z: horizonRows.map(([, row]) => lengths.map((l) => row[l])),
              colorscale: [
                [0, "#b9e5dc"],
                [0.52, "#f8f5de"],
                [1, "#b64e44"],
              ],
              colorbar: { title: { text: "CRPS" } },
            },
          ]}
          layout={{
            xaxis: { title: { text: "Expected Block Length, мес." }, dtick: 1 },
          }}
        />
      </details>
      <details className="calibration-section">
        <summary>Статистические группы и характеристики</summary>
        <ScoreTable
          matrix={result.by_group}
          lengths={lengths}
          label={(id) => GROUP_LABELS[id]}
        />
        <p className="hint">
          Целевой вес группы{" "}
          {scorePercent(Object.values(result.group_weights)[0])}. В недоступных
          группах вес перераспределяется между применимыми.
        </p>
        {Object.entries(GROUP_LABELS).map(([id, label]) => (
          <details key={id} className="calibration-section">
            <summary>{label}</summary>
            <ScoreTable
              matrix={Object.fromEntries(
                Object.entries(result.by_metric).filter(
                  ([metricId]) => result.metrics[metricId].group === id,
                ),
              )}
              lengths={lengths}
              label={(name) =>
                metricLabel(name) +
                (result.metrics[name].primary ? "" : " (диагностика)")
              }
            />
          </details>
        ))}
        <details>
          <summary>
            Применимость, веса и минимальные масштабы для L={length}
          </summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Fold</th>
                  <th>Горизонт</th>
                  <th>Интервалы</th>
                  <th>Рядов / пар в Score</th>
                  <th>Минимальный масштаб</th>
                  {Object.values(GROUP_LABELS).map((name) => (
                    <th key={name}>{name} · вес / метрики</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows
                  .filter((row) => row.length === length)
                  .map((row) => (
                    <tr key={row.fold + ":" + row.horizon}>
                      <td>{row.fold}</td>
                      <td>{row.horizon}</td>
                      <td>{row.intervals}</td>
                      <td>{row.available_members}</td>
                      <td>{row.minimum_scale_count}</td>
                      {Object.keys(GROUP_LABELS).map((id) => (
                        <td key={id}>
                          {scorePercent(row.group_weights[id])} /{" "}
                          {row.primary_metrics[id]}
                        </td>
                      ))}
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </details>
      </details>
      {!holdout && (
        <details className="calibration-section">
          <summary>Устойчивость по Fold и горизонтам</summary>
          <ScoreTable
            matrix={result.by_fold}
            lengths={lengths}
            label={(fold) => "Fold " + fold}
          />
          {Object.entries(result.sensitivity).map(([kind, rows]) => (
            <section key={kind}>
              <h4>
                {kind === "fold"
                  ? "Исключение одного Fold"
                  : "Исключение одного горизонта"}
              </h4>
              <table>
                <thead>
                  <tr>
                    <th>Исключён</th>
                    <th>Best L</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(rows).map(([id, best]) => (
                    <tr key={id}>
                      <td>
                        {kind === "fold" ? "Fold " + id : horizonLabel(id)}
                      </td>
                      <td>{best ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))}
        </details>
      )}
      <details className="calibration-section">
        <summary>Калибровка распределений · PIT для L={length}</summary>
        <p>
          Наблюдений {pit.count} · покрытие P5–P95{" "}
          {scorePercent(pit.p05_p95_coverage)} · покрытие P25–P75{" "}
          {scorePercent(pit.p25_p75_coverage)} · медианное абсолютное отклонение
          ранга от 50% {scorePercent(pit.median_absolute_deviation)}.
        </p>
        <p className="hint">
          Ранги рассчитаны по основным статистикам со средним рангом для
          совпадений. Проверки могут зависеть друг от друга. CRPS отдельных
          метрик доступен в сравнении распределений.
        </p>
        <Plot
          title="PIT histogram"
          height={280}
          data={[
            {
              type: "bar",
              x: pit.histogram.map((_, i) => 0.05 + i / 10),
              y: pit.histogram,
              width: 0.09,
              name: "PIT",
            },
            {
              type: "scatter",
              mode: "lines",
              x: [0, 1],
              y: [pit.count / 10, pit.count / 10],
              name: "Равномерный ориентир",
              line: { dash: "dot" },
            },
          ]}
          layout={{
            xaxis: { tickformat: ".0%", range: [0, 1] },
            yaxis: { title: { text: "Наблюдений" } },
          }}
        />
      </details>
      <CalibrationDistributions
        result={result}
        jobId={jobId}
        demoDetail={demoDetail}
        selectedLength={length}
      />
      {holdout && currentScore && (
        <p>
          Energy Score {scoreNumber(currentScore.energy)} · Variogram Returns{" "}
          {scoreNumber(currentScore.temporal_returns)} · Absolute Returns{" "}
          {scoreNumber(currentScore.temporal_absolute)} · Cross-Asset{" "}
          {scoreNumber(currentScore.cross_asset)}.
        </p>
      )}
      <details className="calibration-section">
        <summary>Источники и воспроизводимость</summary>
        <p className="hint">
          {result.methodology} · расчёт {scoreNumber(result.execution_seconds)}{" "}
          с.
        </p>
        <pre>{JSON.stringify(result.reproducibility, null, 2)}</pre>
      </details>
    </div>
  );
}
