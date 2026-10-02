import { useState } from "react";
import type { ApiRequest, Bounds, Instrument } from "../types";
import { METHODS } from "../types";
import { commonHistory } from "../lib/display";

export default function Settings({
  value,
  instruments,
  locked,
  onChange,
  onPreview,
}: {
  value: ApiRequest;
  instruments: Instrument[];
  locked: boolean;
  onChange: (v: ApiRequest) => void;
  onPreview: () => void;
}) {
  const [query, setQuery] = useState("");
  const selected = instruments.filter((i) =>
    value.instrument_ids.includes(i.id),
  );
  const period = commonHistory(selected);
  const set = <K extends keyof ApiRequest>(key: K, v: ApiRequest[K]) =>
    onChange({ ...value, [key]: v });
  const bound = (
    kind: "asset_constraints" | "group_constraints",
    key: string,
    part: "min" | "max",
    n: number,
  ) =>
    set(kind, {
      ...value[kind],
      [key]: { ...(value[kind][key] ?? { min: 0, max: 1 }), [part]: n / 100 },
    });
  const bounds = (
    kind: "asset_constraints" | "group_constraints",
    key: string,
    label: string,
  ) => {
    const b: Bounds = value[kind][key] ?? { min: 0, max: 1 };
    return (
      <div className="bounds" key={key}>
        <span>{label}</span>
        <input
          aria-label={`${label} минимум %`}
          type="number"
          min="0"
          max="100"
          step="1"
          value={b.min * 100}
          onChange={(e) => bound(kind, key, "min", +e.target.value)}
        />
        <input
          aria-label={`${label} максимум %`}
          type="number"
          min="0"
          max="100"
          step="1"
          value={b.max * 100}
          onChange={(e) => bound(kind, key, "max", +e.target.value)}
        />
      </div>
    );
  };
  return (
    <div className="settings">
      <fieldset disabled={locked}>
        <h2>Настройки расчёта</h2>
        <label>
          Поиск инструментов
          <input
            type="search"
            placeholder="Тикер или название"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="asset-list">
          {instruments
            .filter((i) =>
              `${i.ticker} ${i.name}`
                .toLowerCase()
                .includes(query.toLowerCase()),
            )
            .map((i) => (
              <label className="asset-option" key={i.id}>
                <input
                  type="checkbox"
                  checked={value.instrument_ids.includes(i.id)}
                  disabled={
                    !value.instrument_ids.includes(i.id) &&
                    value.instrument_ids.length >= 15
                  }
                  onChange={(e) => {
                    const ids = e.target.checked
                      ? [...value.instrument_ids, i.id]
                      : value.instrument_ids.filter((id) => id !== i.id);
                    const ac = Object.fromEntries(
                      Object.entries(value.asset_constraints).filter(([id]) =>
                        ids.includes(+id),
                      ),
                    );
                    const groups = new Set(
                      instruments
                        .filter((x) => ids.includes(x.id))
                        .flatMap((x) => x.groups),
                    );
                    onChange({
                      ...value,
                      instrument_ids: ids,
                      start_date: null,
                      end_date: null,
                      asset_constraints: ac,
                      group_constraints: Object.fromEntries(
                        Object.entries(value.group_constraints).filter(
                          ([group]) => groups.has(group),
                        ),
                      ),
                    });
                  }}
                />
                <span>
                  <b>{i.ticker}</b>
                  <small>
                    {i.name} · {i.start_date.slice(0, 7)}—
                    {i.end_date.slice(0, 7)}
                  </small>
                </span>
              </label>
            ))}
        </div>
        <p className="hint">
          Выбрано {value.instrument_ids.length} / 15 · минимум 2. Только USD.
        </p>
        <p className="period">
          Общая история:{" "}
          <b>
            {period ? `${period.start} — ${period.end}` : "нет пересечения"}
          </b>
        </p>
        <div className="two">
          <label>
            Начало
            <input
              type="date"
              value={value.start_date ?? ""}
              onChange={(e) => set("start_date", e.target.value || null)}
            />
          </label>
          <label>
            Конец
            <input
              type="date"
              value={value.end_date ?? ""}
              onChange={(e) => set("end_date", e.target.value || null)}
            />
          </label>
        </div>
        <button className="secondary full" type="button" onClick={onPreview}>
          Проверить период и пропуски
        </button>
        <label>
          Метод ковариации
          <select
            value={value.covariance_method}
            onChange={(e) =>
              set(
                "covariance_method",
                e.target.value as ApiRequest["covariance_method"],
              )
            }
          >
            {METHODS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        {value.covariance_method === "cv_linear" && (
          <label>
            Цель shrinkage
            <select
              value={value.shrinkage_target}
              onChange={(e) =>
                set(
                  "shrinkage_target",
                  e.target.value as ApiRequest["shrinkage_target"],
                )
              }
            >
              <option value="identity">Scaled identity</option>
              <option value="diagonal">Diagonal · variances preserved</option>
            </select>
          </label>
        )}
        <div className="two">
          <label>
            Безрисковая ставка, %
            <input
              type="number"
              min="-100"
              max="100"
              step="0.1"
              value={value.risk_free_rate * 100}
              onChange={(e) => set("risk_free_rate", +e.target.value / 100)}
            />
          </label>
          <label>
            CV-фолды
            <input
              type="number"
              min="2"
              max="10"
              value={value.cv_folds}
              onChange={(e) => set("cv_folds", +e.target.value)}
            />
          </label>
        </div>
        <p className="hint">
          Ставка постоянная годовая; исторический ряд ставки не используется.
        </p>
        <label>
          Цель Frontier / CV
          <select
            value={value.objective}
            onChange={(e) =>
              onChange({
                ...value,
                objective: e.target.value as ApiRequest["objective"],
                target_return:
                  e.target.value === "target_return"
                    ? (value.target_return ?? 0.08)
                    : null,
              })
            }
          >
            <option value="gmv">GMV · minimum variance</option>
            <option value="max_sharpe">Maximum Sharpe</option>
            <option value="target_return">Target expected return</option>
          </select>
        </label>
        {value.objective === "target_return" && (
          <label>
            Целевая годовая доходность, %
            <input
              type="number"
              value={(value.target_return ?? 0) * 100}
              onChange={(e) => set("target_return", +e.target.value / 100)}
            />
          </label>
        )}
        <label>
          Точки Frontier
          <input
            type="number"
            min="2"
            max="201"
            value={value.frontier_points}
            onChange={(e) => set("frontier_points", +e.target.value)}
          />
        </label>
        <details open>
          <summary>Индивидуальные ограничения</summary>
          <div className="bounds heading">
            <span>Актив</span>
            <span>Min %</span>
            <span>Max %</span>
          </div>
          {selected.map((i) =>
            bounds("asset_constraints", String(i.id), i.ticker),
          )}
        </details>
        <details>
          <summary>Групповые ограничения</summary>
          <p className="hint">Группы из каталога; могут пересекаться.</p>
          <div className="bounds heading">
            <span>Группа</span>
            <span>Min %</span>
            <span>Max %</span>
          </div>
          {[...new Set(selected.flatMap((i) => i.groups))]
            .sort()
            .map((g) => bounds("group_constraints", g, g))}
        </details>
        <details open>
          <summary>Stationary Bootstrap</summary>
          <div className="two">
            <label>
              Повторений
              <input
                type="number"
                min="1"
                max="2000"
                value={value.bootstrap_iterations}
                onChange={(e) => set("bootstrap_iterations", +e.target.value)}
              />
            </label>
            <label>
              Длина блока, мес.
              <input
                type="number"
                min="3"
                max="24"
                value={value.expected_block_length}
                onChange={(e) => set("expected_block_length", +e.target.value)}
              />
            </label>
          </div>
          <label>
            Random seed
            <input
              type="number"
              min="0"
              max="4294967295"
              value={value.random_seed}
              onChange={(e) => set("random_seed", +e.target.value)}
            />
          </label>
          <p className="hint">
            Bootstrap рассчитывает GMV и Max Sharpe. Resampled Frontier: не
            более 500 повторений; задайте число явно.
          </p>
        </details>
      </fieldset>
    </div>
  );
}
