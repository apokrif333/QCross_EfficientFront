import type {
  ApiRequest,
  Instrument,
  PortfolioControls as Config,
} from "../types";
import { useState } from "react";

export default function PortfolioControls({
  request,
  instruments,
  onChange,
  locked,
}: {
  request: ApiRequest;
  instruments: Instrument[];
  onChange: (r: ApiRequest) => void;
  locked: boolean;
}) {
  const c = request.portfolio_controls ?? { mode: "none", hedge_weights: {} };
  const [hedgeQuery, setHedgeQuery] = useState("");
  const hedgeMatch = instruments.find(
    (i) =>
      i.ticker.toLowerCase() === hedgeQuery.trim().toLowerCase() ||
      String(i.id) === hedgeQuery.trim(),
  );
  const hedgeId =
    hedgeQuery.trim().toUpperCase() === "BIL_REFERENCE"
      ? "BIL_REFERENCE"
      : hedgeMatch
        ? String(hedgeMatch.id)
        : null;
  const set = (next: Config) =>
    onChange({ ...request, portfolio_controls: next });
  return (
    <section className="subsection">
      <h3>Управление портфелем</h3>
      <fieldset disabled={locked}>
        <label>
          Режим
          <select
            value={c.mode}
            onChange={(e) =>
              set({
                ...c,
                mode: e.target.value as Config["mode"],
                trend: c.trend ?? { destination: "remaining", asset_rules: {} },
                downside_vol: c.downside_vol ?? {
                  target_annual: 0.09,
                  lookback_months: 12,
                },
              })
            }
          >
            <option value="none">Без управления</option>
            <option value="downside_vol">Target Downside Volatility</option>
            <option value="trend">SMA / Momentum по активам</option>
          </select>
        </label>
        {c.mode !== "none" && (
          <>
            {c.mode === "downside_vol" && (
              <>
                <label>
                  Целевая downside volatility, %
                  <input
                    type="number"
                    min="0.01"
                    value={(c.downside_vol?.target_annual ?? 0.09) * 100}
                    onChange={(e) =>
                      set({
                        ...c,
                        downside_vol: {
                          ...c.downside_vol!,
                          target_annual: +e.target.value / 100,
                        },
                      })
                    }
                  />
                </label>
                <label>
                  Период, месяцев
                  <input
                    type="number"
                    min="3"
                    max="24"
                    value={c.downside_vol?.lookback_months ?? 12}
                    onChange={(e) =>
                      set({
                        ...c,
                        downside_vol: {
                          ...c.downside_vol!,
                          lookback_months: +e.target.value,
                        },
                      })
                    }
                  />
                </label>
                <p className="hint">
                  Масштабирование экспозиции, без гарантии ограничения
                  фактического риска. Хедж: доли должны составлять 100%.
                </p>
              </>
            )}
            {c.mode === "trend" && c.trend && (
              <>
                <label>
                  Куда направлять исключённый капитал
                  <select
                    value={c.trend.destination}
                    onChange={(e) =>
                      set({
                        ...c,
                        trend: {
                          ...c.trend!,
                          destination: e.target.value as "remaining" | "hedge",
                        },
                      })
                    }
                  >
                    <option value="remaining">Оставшиеся активы</option>
                    <option value="hedge">Хедж-портфель</option>
                  </select>
                </label>
                {request.instrument_ids.map((id) => {
                  const key = String(id);
                  const rule = c.trend!.asset_rules[key] ?? {
                    indicator: "disabled",
                    lookback_months: 12,
                  };
                  return (
                    <div className="two" key={id}>
                      <label>
                        {instruments.find((i) => i.id === id)?.ticker ?? id}
                        <select
                          value={rule.indicator}
                          onChange={(e) =>
                            set({
                              ...c,
                              trend: {
                                ...c.trend!,
                                asset_rules: {
                                  ...c.trend!.asset_rules,
                                  [key]: {
                                    ...rule,
                                    indicator: e.target
                                      .value as typeof rule.indicator,
                                  },
                                },
                              },
                            })
                          }
                        >
                          <option value="disabled">Отключено</option>
                          <option value="sma">SMA</option>
                          <option value="momentum">Momentum</option>
                        </select>
                      </label>
                      <label>
                        Период, месяцев
                        <input
                          type="number"
                          min="2"
                          max="36"
                          value={rule.lookback_months}
                          disabled={rule.indicator === "disabled"}
                          onChange={(e) =>
                            set({
                              ...c,
                              trend: {
                                ...c.trend!,
                                asset_rules: {
                                  ...c.trend!.asset_rules,
                                  [key]: {
                                    ...rule,
                                    lookback_months: +e.target.value,
                                  },
                                },
                              },
                            })
                          }
                        />
                      </label>
                    </div>
                  );
                })}
                <label>
                  Актив для 3D-чувствительности периода
                  <select
                    value={c.trend.sensitivity_asset ?? ""}
                    onChange={(e) =>
                      set({
                        ...c,
                        trend: {
                          ...c.trend!,
                          sensitivity_asset: e.target.value || null,
                        },
                      })
                    }
                  >
                    <option value="">Не рассчитывать</option>
                    {request.instrument_ids
                      .filter(
                        (id) =>
                          c.trend!.asset_rules[String(id)]?.indicator &&
                          c.trend!.asset_rules[String(id)].indicator !==
                            "disabled",
                      )
                      .map((id) => (
                        <option key={id} value={id}>
                          {instruments.find((i) => i.id === id)?.ticker ?? id}
                        </option>
                      ))}
                  </select>
                </label>
                <p className="hint">
                  При отсутствии активных позиций весь капитал направляется в
                  заданный хедж. Чувствительность: 35 расчётов для одного
                  актива, периоды 2–36 месяцев, после запуска расчёта.
                </p>
              </>
            )}
            <datalist id="hedge-instruments">
              <option value="BIL_REFERENCE">BIL · исторический USD</option>
              {instruments.map((i) => (
                <option key={i.id} value={i.ticker}>
                  {i.ticker} · {i.name}
                </option>
              ))}
            </datalist>
            {Object.entries(c.hedge_weights).map(([a, w]) => (
              <div className="two" key={a}>
                <span>
                  {instruments.find((i) => String(i.id) === a)?.ticker ?? a}
                </span>
                <input
                  aria-label={`Хедж ${a}, %`}
                  type="number"
                  min="0"
                  max="100"
                  value={w * 100}
                  onChange={(e) =>
                    set({
                      ...c,
                      hedge_weights: {
                        ...c.hedge_weights,
                        [a]: +e.target.value / 100,
                      },
                    })
                  }
                />
                <button
                  onClick={() => {
                    const weights = { ...c.hedge_weights };
                    delete weights[a];
                    set({ ...c, hedge_weights: weights });
                  }}
                >
                  Удалить
                </button>
              </div>
            ))}
            <label>
              Добавить инструмент хеджа
              <input
                list="hedge-instruments"
                placeholder="Тикер или ID · BIL_REFERENCE"
                value={hedgeQuery}
                onChange={(e) => setHedgeQuery(e.target.value)}
              />
            </label>
            <button
              disabled={!hedgeId}
              onClick={() => {
                if (hedgeId) {
                  set({
                    ...c,
                    hedge_weights: {
                      ...c.hedge_weights,
                      [hedgeId]: c.hedge_weights[hedgeId] ?? 0,
                    },
                  });
                  setHedgeQuery("");
                }
              }}
            >
              Добавить в хедж
            </button>
          </>
        )}
      </fieldset>
    </section>
  );
}
