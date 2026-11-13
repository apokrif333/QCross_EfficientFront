import type {
  ApiRequest,
  Instrument,
  PortfolioControls as Config,
} from "../types";

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
                downside_vol: c.downside_vol ?? {
                  target_annual: 0.09,
                  lookback_months: 12,
                },
              })
            }
          >
            <option value="none">Без управления</option>
            <option value="downside_vol">Target Downside Volatility</option>
          </select>
        </label>
        {c.mode !== "none" && (
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
              Масштабирование экспозиции, без гарантии ограничения фактического
              риска. Хедж: доли должны составлять 100%.
            </p>
            <datalist id="hedge-instruments">
              <option value="BIL_REFERENCE">BIL · исторический USD</option>
              {instruments.map((i) => (
                <option key={i.id} value={String(i.id)}>
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
                onChange={(e) => {
                  const v = e.target.value;
                  const match = instruments.find(
                    (i) =>
                      i.ticker.toLowerCase() === v.toLowerCase() ||
                      String(i.id) === v,
                  );
                  const id =
                    v === "BIL_REFERENCE" ? v : match ? String(match.id) : null;
                  if (id) {
                    set({
                      ...c,
                      hedge_weights: { ...c.hedge_weights, [id]: 0 },
                    });
                    e.target.value = "";
                  }
                }}
              />
            </label>
          </>
        )}
      </fieldset>
    </section>
  );
}
