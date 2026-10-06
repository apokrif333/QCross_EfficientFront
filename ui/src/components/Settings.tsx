import type { ApiRequest, Instrument } from "../types";
import { METHODS } from "../types";
import { commonHistory } from "../lib/display";
import HelpField from "./HelpField";

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
  const selected = instruments.filter((i) =>
      value.instrument_ids.includes(i.id),
    ),
    period = commonHistory(selected);
  const groups = [
    ...new Set(
      value.instrument_ids.map((id) => value.asset_groups?.[String(id)] ?? 1),
    ),
  ].sort();
  const legacy = Object.fromEntries(
    Object.entries(value.group_constraints).filter(
      ([key]) => !/^Group [1-5]$/.test(key),
    ),
  );
  const set = <K extends keyof ApiRequest>(key: K, v: ApiRequest[K]) =>
    onChange({ ...value, [key]: v });
  return (
    <div className="settings">
      <fieldset disabled={locked}>
        <h2>Групповые ограничения</h2>
        <div className="bounds heading">
          <span>Группа</span>
          <span>Min %</span>
          <span>Max %</span>
        </div>
        {groups.map((g) => {
          const name = `Group ${g}`,
            b = value.group_constraints[name] ?? { min: 0, max: 1 };
          return (
            <div className="bounds" key={g}>
              <span>Группа {g}</span>
              {(["min", "max"] as const).map((k) => (
                <input
                  key={k}
                  aria-label={`Группа ${g} ${k === "min" ? "минимум" : "максимум"} %`}
                  type="number"
                  min="0"
                  max="100"
                  step="any"
                  value={b[k] * 100}
                  onChange={(e) =>
                    set("group_constraints", {
                      ...value.group_constraints,
                      [name]: { ...b, [k]: +e.target.value / 100 },
                    })
                  }
                />
              ))}
            </div>
          );
        })}
        {!!Object.keys(legacy).length && (
          <details>
            <summary>Ограничения сохранённого сценария</summary>
            {Object.entries(legacy).map(([name, b]) => (
              <p className="hint" key={name}>
                {name}: {b.min * 100}–{b.max * 100}%
              </p>
            ))}
          </details>
        )}
        <p className="period">
          Общая исходная история:
          <b>
            {period ? `${period.start} — ${period.end}` : "нет пересечения"}
          </b>
        </p>
        <div className="two">
          <HelpField
            label="Начало"
            help="Первый месяц расчёта. Пустое поле использует начало общей истории выбранных активов."
          >
            <input
              type="date"
              value={value.start_date ?? ""}
              onChange={(e) => set("start_date", e.target.value || null)}
            />
          </HelpField>
          <HelpField
            label="Конец"
            help="Последний месяц расчёта. Пустое поле использует конец общей истории выбранных активов."
          >
            <input
              type="date"
              value={value.end_date ?? ""}
              onChange={(e) => set("end_date", e.target.value || null)}
            />
          </HelpField>
        </div>
        <button className="secondary full" type="button" onClick={onPreview}>
          Проверить период и пропуски
        </button>
        <HelpField
          label="Метод ковариации"
          help="Способ оценки риска и совместных изменений активов. Используется в Frontier, Cross-validation, Bootstrap и Resampled Frontier."
        >
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
              <option value={m.value} key={m.value}>
                {m.name}
              </option>
            ))}
          </select>
        </HelpField>
        {value.covariance_method === "cv_linear" && (
          <HelpField
            label="Цель shrinkage"
            help="Матрица, к которой приближается выборочная ковариация. Scaled identity использует общую среднюю дисперсию; Diagonal сохраняет отдельные дисперсии активов. Интенсивность выбирается по проверочным данным."
          >
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
          </HelpField>
        )}
        <div className="two">
          <HelpField
            label="Безрисковая ставка, %"
            help="Постоянная годовая ставка для Sharpe: вычитается из арифметической ожидаемой доходности. Это заданное значение, а не исторический ряд ставок."
          >
            <input
              type="number"
              min="-100"
              max="100"
              step="any"
              value={value.risk_free_rate * 100}
              onChange={(e) => set("risk_free_rate", +e.target.value / 100)}
            />
          </HelpField>
          <HelpField
            label="Точки Frontier"
            help="Число оптимизированных портфелей вдоль кривой от GMV до максимальной достижимой ожидаемой доходности. По умолчанию 51 точка."
          >
            <input
              type="number"
              min="2"
              max="201"
              value={value.frontier_points}
              onChange={(e) => set("frontier_points", +e.target.value)}
            />
          </HelpField>
        </div>
        <HelpField
          label="Цель оптимизации для CV"
          help="Выбирает портфель для обучения на CV-фолдах: GMV, Max Sharpe или заданная арифметическая доходность. Frontier независимо рассчитывает GMV, Max Sharpe и всю кривую target returns. В API эта цель также выбирает дополнительный selected_portfolio."
        >
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
        </HelpField>
        {value.objective === "target_return" && (
          <HelpField
            label="Целевая арифметическая доходность, %"
            help="Годовая арифметическая доходность, которую должен иметь портфель при обучении на каждом CV-фолде. Это не CAGR. Если цель недостижима, фолд возвращает ошибку."
          >
            <input
              type="number"
              value={(value.target_return ?? 0) * 100}
              onChange={(e) => set("target_return", +e.target.value / 100)}
            />
          </HelpField>
        )}
        <HelpField
          label="Число CV-фолдов"
          help="Cross-validation: число внешних фолдов. Для CV LS это также число внутренних фолдов при выборе интенсивности shrinkage, в том числе на Frontier. В каждом внешнем фолде подбор использует только обучающие данные."
        >
          <input
            type="number"
            min="2"
            max="10"
            value={value.cv_folds}
            onChange={(e) => set("cv_folds", +e.target.value)}
          />
        </HelpField>
        <details open>
          <summary>Stationary Bootstrap</summary>
          <div className="two">
            <HelpField
              label="Повторений"
              help="Число Stationary Bootstrap выборок. Bootstrap рассчитывает GMV и Max Sharpe; по умолчанию 1000 повторений для каждой цели. Для Resampled Frontier укажите до 500 повторений; стандартный сценарий — 250."
            >
              <input
                type="number"
                min="1"
                max="2000"
                value={value.bootstrap_iterations}
                onChange={(e) => set("bootstrap_iterations", +e.target.value)}
              />
            </HelpField>
            <HelpField
              label="Длина блока, мес."
              help="Ожидаемая длина блока Stationary Bootstrap: 3–24 месяца. Вероятность начать новый блок равна 1 / длина блока. Все активы пересэмплируются совместно."
            >
              <input
                type="number"
                min="3"
                max="24"
                step="any"
                value={value.expected_block_length}
                onChange={(e) => set("expected_block_length", +e.target.value)}
              />
            </HelpField>
          </div>
          <HelpField
            label="Random seed"
            help="Начальное значение генератора случайных чисел. Одинаковые данные, настройки и seed дают воспроизводимые bootstrap выборки."
          >
            <input
              type="number"
              min="0"
              max="4294967295"
              value={value.random_seed}
              onChange={(e) => set("random_seed", +e.target.value)}
            />
          </HelpField>
        </details>
      </fieldset>
    </div>
  );
}
