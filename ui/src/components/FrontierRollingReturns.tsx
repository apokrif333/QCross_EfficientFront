import { useMemo, useState } from "react";
import type { Data, Layout } from "plotly.js";
import type { FrontierResult } from "../types";
import { number, percent } from "../lib/display";
import {
  rollingReturnDifferences,
  rollingReturns,
} from "../lib/rollingReturns";
import { linearQuantile } from "../lib/statistics";
import Plot from "./Plot";

function availablePaths(result: FrontierResult) {
  const control = result.portfolio_controls;
  const passive = control?.baseline?.net_wealth;
  const managed = control?.controlled?.net_wealth;
  const user = result.user_portfolio?.metrics?.wealth_history;
  const optimized = result.selected_portfolio.metrics?.wealth_history;
  const equal = result.equal_weight.metrics?.wealth_history;
  const candidates = [
    {
      id: "managed",
      name: "Ваш портфель под управлением",
      months: control?.months,
      wealth: managed,
    },
    {
      id: "passive",
      name: "Ваш пассивный портфель",
      months: passive ? control?.months : user?.months,
      wealth: passive ?? user?.net,
    },
    ...(Array.isArray(passive) &&
    Array.isArray(managed) &&
    passive.length === managed.length
      ? [
          {
            id: "passive-vs-managed",
            name: "Пассивный − управляемый",
            months: control?.months,
            wealth: passive,
            managedWealth: managed,
          },
        ]
      : []),
    {
      id: "optimized",
      name: "Портфель по выбранной цели",
      months: optimized?.months,
      wealth: optimized?.net,
    },
    {
      id: "equal",
      name: "Равновзвешенный портфель",
      months: equal?.months,
      wealth: equal?.net,
    },
  ];
  return candidates.flatMap(({ months, wealth, ...path }) =>
    Array.isArray(months) &&
    Array.isArray(wealth) &&
    wealth.length === months.length + 1 &&
    months.length >= 12
      ? [{ ...path, months, wealth }]
      : [],
  );
}

const colors = {
  positive: "#267f81",
  negative: "#ad504d",
  flat: "#85979f",
};
const valueColor = (value: number) =>
  value > 0 ? colors.positive : value < 0 ? colors.negative : colors.flat;
const layout: Partial<Layout> = {
  xaxis: { type: "date", title: { text: "Конец окна" } },
  yaxis: {
    title: { text: "Годовая доходность" },
    tickformat: ".0%",
    zeroline: true,
    zerolinecolor: "#899ea7",
  },
  dragmode: "zoom",
  bargap: 0.08,
};
const comparisonLayout: Partial<Layout> = {
  ...layout,
  yaxis: {
    ...layout.yaxis,
    title: { text: "Разница доходности, п. п." },
    tickformat: ".3~g",
  },
};
const dateRange = ({ start, end }: { start: string; end: string }) =>
  `${start} — ${end}`;

export default function FrontierRollingReturns({
  result,
}: {
  result: FrontierResult;
}) {
  const paths = availablePaths(result);
  const [pathId, setPathId] = useState(paths[0]?.id);
  const path = paths.find(({ id }) => id === pathId) ?? paths[0];
  const managedWealth =
    path && "managedWealth" in path ? path.managedWealth : undefined;
  const comparing = managedWealth !== undefined;
  const maxYears = Math.floor((path?.months.length ?? 0) / 12);
  const [yearsInput, setYearsInput] = useState(String(Math.min(5, maxYears)));
  const [lowInput, setLowInput] = useState("15");
  const [highInput, setHighInput] = useState("85");
  const years = Number(yearsInput);
  const validYears = Number.isInteger(years) && years >= 1 && years <= maxYears;
  const { points, customdata } = useMemo(() => {
    if (!path) return { points: [], customdata: [] };
    if (managedWealth) {
      const points = rollingReturnDifferences(
        path.months,
        path.wealth,
        managedWealth,
        years,
      );
      return {
        points,
        customdata: points.map((point) => [
          dateRange(point),
          point.passive,
          point.managed,
        ]),
      };
    }
    const points = rollingReturns(path.months, path.wealth, years);
    return { points, customdata: points.map(dateRange) };
  }, [path?.months, path?.wealth, managedWealth, years]);
  const bars = useMemo<Data[]>(
    () => [
      {
        type: "bar",
        x: points.map(({ end }) => end),
        y: points.map(({ value }) => (comparing ? value * 100 : value)),
        customdata,
        marker: {
          color: points.map(({ value }) => valueColor(value)),
        },
        hovertemplate: comparing
          ? "%{customdata[0]}<br>Пассивный: %{customdata[1]:.2%}<br>Управляемый: %{customdata[2]:.2%}<br>Разница: %{y:+.2f} п. п.<extra></extra>"
          : "%{customdata}<br>Годовая доходность: %{y:.2%}<extra></extra>",
      },
    ],
    [points, customdata, comparing],
  );
  const sorted = useMemo(
    () => [...points].sort((a, b) => a.value - b.value),
    [points],
  );
  const values = sorted.map((point) => point.value);
  const positive = values.filter((value) => value > 0).length;
  const negative = values.filter((value) => value < 0).length;
  const flat = values.length - positive - negative;
  const low = Number(lowInput);
  const high = Number(highInput);
  const validPercentiles =
    lowInput !== "" &&
    highInput !== "" &&
    Number.isInteger(low) &&
    Number.isInteger(high) &&
    low >= 0 &&
    low <= high &&
    high <= 100;
  const formatValue = (value: number) => (
    <b style={comparing ? { color: valueColor(value) } : undefined}>
      {comparing
        ? `${value > 0 ? "+" : ""}${number(value * 100, 2)} п. п.`
        : percent(value)}
    </b>
  );
  const tail = (p: number) => {
    const threshold = linearQuantile(values, p / 100)!;
    const atOrAbove = values.filter((value) => value >= threshold).length;
    return (
      <>
        {formatValue(threshold)}
        <small>Не ниже порога: {percent(atOrAbove / values.length)}</small>
      </>
    );
  };

  return (
    <section
      className="subsection rolling-returns"
      aria-label="Скользящая доходность"
    >
      <h3>Скользящая доходность</h3>
      {path ? (
        <>
          <div className="rolling-controls">
            {paths.length > 1 && (
              <label>
                Портфель
                <select
                  value={path.id}
                  onChange={(event) => {
                    const next = paths.find(
                      ({ id }) => id === event.target.value,
                    )!;
                    setPathId(next.id);
                    const limit = Math.floor(next.months.length / 12);
                    if (years > limit) setYearsInput(String(limit));
                  }}
                >
                  {paths.map(({ id, name }) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              Горизонт, лет
              <input
                type="number"
                min={1}
                max={maxYears}
                step={1}
                value={yearsInput}
                onChange={(event) => setYearsInput(event.target.value)}
                aria-invalid={!validYears}
              />
            </label>
          </div>
          <p className="hint">
            {comparing
              ? "Разница годовых доходностей после издержек за одинаковые окна. Значения выше нуля означают преимущество пассивного портфеля."
              : "Годовая доходность после комиссий. Начало каждого следующего окна сдвигается на один месяц."}
          </p>
          {!validYears ? (
            <p role="status">Введите целое число лет от 1 до {maxYears}.</p>
          ) : points.length === 0 ? (
            <p role="status">
              {comparing
                ? "Нет окон с доступной доходностью обоих портфелей за этот горизонт."
                : "Нет окон с доступной доходностью за этот горизонт."}
            </p>
          ) : (
            <>
              <Plot
                title={
                  comparing
                    ? "Разница скользящей доходности"
                    : "Годовая скользящая доходность"
                }
                data={bars}
                layout={comparing ? comparisonLayout : layout}
              />
              <div className="rolling-summary">
                <div className="rolling-controls">
                  <label>
                    Нижний персентиль
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={lowInput}
                      onChange={(event) => setLowInput(event.target.value)}
                      aria-invalid={!validPercentiles}
                    />
                  </label>
                  <label>
                    Верхний персентиль
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={highInput}
                      onChange={(event) => setHighInput(event.target.value)}
                      aria-invalid={!validPercentiles}
                    />
                  </label>
                </div>
                {!validPercentiles && (
                  <p role="status">
                    Введите перцентили от 0 до 100 по возрастанию.
                  </p>
                )}
                <dl className="rolling-stats">
                  <div>
                    <dt>Худшее окно</dt>
                    <dd>
                      {formatValue(sorted[0].value)}
                      <small>{dateRange(sorted[0])}</small>
                    </dd>
                  </div>
                  <div>
                    <dt>
                      {validPercentiles ? `P${low}` : "Нижний персентиль"}
                    </dt>
                    <dd>{validPercentiles ? tail(low) : "—"}</dd>
                  </div>
                  <div>
                    <dt>Медиана</dt>
                    <dd>{tail(50)}</dd>
                  </div>
                  <div>
                    <dt>
                      {validPercentiles ? `P${high}` : "Верхний персентиль"}
                    </dt>
                    <dd>{validPercentiles ? tail(high) : "—"}</dd>
                  </div>
                  <div>
                    <dt>Лучшее окно</dt>
                    <dd>
                      {formatValue(sorted.at(-1)!.value)}
                      <small>{dateRange(sorted.at(-1)!)}</small>
                    </dd>
                  </div>
                  <div>
                    <dt>Всего окон: {values.length}</dt>
                    <dd>
                      <div className="rolling-occurrences" aria-hidden="true">
                        <span
                          style={{
                            flexGrow: positive,
                            background: colors.positive,
                          }}
                        />
                        <span
                          style={{ flexGrow: flat, background: colors.flat }}
                        />
                        <span
                          style={{
                            flexGrow: negative,
                            background: colors.negative,
                          }}
                        />
                      </div>
                      <small>
                        {comparing ? "Пассивный выше" : "Положительные"}:{" "}
                        {positive} ({percent(positive / values.length)})
                      </small>
                      <small>
                        {comparing ? "Управляемый выше" : "Отрицательные"}:{" "}
                        {negative} ({percent(negative / values.length)})
                      </small>
                      {flat > 0 && (
                        <small>
                          {comparing ? "Одинаково" : "Нулевые"}: {flat} (
                          {percent(flat / values.length)})
                        </small>
                      )}
                    </dd>
                  </div>
                </dl>
              </div>
            </>
          )}
        </>
      ) : (
        <p className="hint">
          Для графика нужен расчёт Frontier с сохранённой историей капитала.
        </p>
      )}
    </section>
  );
}
