import { useEffect, useRef, useState } from "react";
import type { ApiRequest, Instrument, Job, Mode } from "../types";
import {
  cancelCalibration,
  previewCalibration,
  requestJson,
  runCalibration,
} from "../lib/api";
import {
  CALIBRATION_DEFAULTS,
  CALIBRATION_HORIZONS,
  horizonLabel,
  type CalibrationDemo,
  type CalibrationPreview,
  type CalibrationRequest,
  type CalibrationResult,
} from "../lib/calibration";
import CalibrationResults from "./CalibrationResults";

type RecordedResult = { result: CalibrationResult; jobId?: string };
type Operation = "preview" | "calibration" | "holdout";
type HoldoutRecord = {
  assets: string;
  start: string;
  end: string;
  calibration: unknown;
};
const HOLDOUT_KEY = "qcross-bootstrap-calibration-holdouts-v1";

function CalibrationProgress({
  name,
  job,
  cancelling,
  onCancel,
}: {
  name: string;
  job?: Job<CalibrationResult>;
  cancelling: boolean;
  onCancel: () => void;
}) {
  const progress = job?.progress;
  const percent = progress?.total_steps
    ? Math.floor((100 * progress.completed_steps) / progress.total_steps)
    : undefined;
  const stages = {
    starting: "Запускаем расчёт",
    reference: "Готовим данные Fold и эталонные сценарии",
    scoring: "Оцениваем длину блока L=" + progress?.block_length + " мес.",
    summarizing: "Формируем сводку и диагностику",
    transferring: "Передаём результаты расчёта",
    completed: "Загружаем результаты",
  };
  const stage = progress
    ? stages[progress.phase]
    : job
      ? "Выполняется расчёт"
      : "Отправляем задачу";
  const fold =
    progress?.fold_index && progress.total_folds
      ? "Fold " + progress.fold_index + " из " + progress.total_folds + " · "
      : "";
  return (
    <div className="calibration-progress">
      <div className="calibration-progress-heading">
        <strong>{name}</strong>
        <span>
          {percent !== undefined && percent + "%"}
          {percent !== undefined && job && " · "}
          {job && Math.floor(job.elapsed_seconds) + " с"}
        </span>
      </div>
      <progress aria-label={"Прогресс " + name} max={100} value={percent} />
      <div className="calibration-progress-heading">
        <span role="status">
          {fold}
          {stage}
        </span>
        {job?.status === "running" && (
          <button disabled={cancelling} onClick={onCancel}>
            {cancelling ? "Отменяем задачу…" : "Отменить задачу"}
          </button>
        )}
      </div>
    </div>
  );
}

function rememberedHoldout(
  result: CalibrationResult,
  source: CalibrationResult,
) {
  const test = source.preview.holdout?.test;
  if (!test) return result;
  const assets = source.preview.assets
    .map((a) => a.instrument_id)
    .sort((a, b) => a - b)
    .join(",");
  const calibration = source.reproducibility.calculation_sha256;
  try {
    const records = JSON.parse(
      localStorage.getItem(HOLDOUT_KEY) ?? "[]",
    ) as HoldoutRecord[];
    const reused = records.some(
      (entry) =>
        entry.assets === assets &&
        entry.start <= test.end &&
        entry.end >= test.start &&
        entry.calibration !== calibration,
    );
    if (!records.some((entry) => entry.calibration === calibration))
      localStorage.setItem(
        HOLDOUT_KEY,
        JSON.stringify([
          ...records.slice(-99),
          { ...test, assets, calibration },
        ]),
      );
    return reused
      ? { ...result, holdout_status: "previously_viewed" as const }
      : result;
  } catch {
    return result;
  }
}

const fields = [
  { key: "train_length", label: "Train, мес.", min: 24, max: 1200 },
  { key: "test_length", label: "Test, мес.", min: 3, max: 1200 },
  { key: "step", label: "Шаг Fold, мес.", min: 3, max: 1200 },
  { key: "generations", label: "Bootstrap-генераций", min: 2, max: 2000 },
  {
    key: "min_block_length",
    label: "Минимальная длина блока",
    min: 1,
    max: 24,
  },
  {
    key: "max_block_length",
    label: "Максимальная длина блока",
    min: 1,
    max: 24,
  },
  { key: "block_length_step", label: "Шаг длины блока", min: 1, max: 23 },
  {
    key: "reference_block_length",
    label: "Длина блока для нормализации",
    min: 1,
    max: 120,
  },
  { key: "random_seed", label: "Random Seed", min: 0, max: 4294967295 },
] as const;

function Preview({ preview }: { preview: CalibrationPreview }) {
  const rows = [
    ...preview.folds,
    ...(preview.holdout ? [preview.holdout] : []),
  ];
  return (
    <div className="calibration-section">
      <p>
        Общая история {preview.history.start} — {preview.history.end} (
        {preview.history.months} мес.) · полных Calibration Fold{" "}
        {preview.full_calibration_folds} ·{" "}
        {preview.generations.toLocaleString("ru-RU")} генераций на L и Fold ·{" "}
        {preview.trajectory_count.toLocaleString("ru-RU")} оцениваемых
        траекторий.
      </p>
      {preview.warnings.map((warning) => (
        <p className="warning" key={warning}>
          {warning}
        </p>
      ))}
      <p className="hint">
        Ряды QCross · {preview.assets.map((asset) => asset.ticker).join(", ")}.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Период</th>
              <th>Train</th>
              <th>Реальный Test</th>
              <th>Месяцев Test</th>
              {preview.horizons.map((h) => (
                <th key={h}>{horizonLabel(h)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((fold) => (
              <tr key={fold.fold}>
                <th>{fold.holdout ? "Final Holdout" : "Fold " + fold.fold}</th>
                <td>
                  {fold.train.start} — {fold.train.end}
                </td>
                <td>
                  {fold.test.start} — {fold.test.end}
                </td>
                <td>{fold.test.months}</td>
                {preview.horizons.map((h) => (
                  <td key={h}>{fold.intervals[h]}</td>
                ))}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th colSpan={4}>Интервалы Calibration</th>
              {preview.horizons.map((h) => (
                <td key={h}>{preview.interval_counts[h]}</td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

export default function BootstrapCalibration({
  request,
  mode,
  instruments,
  onUseLength,
}: {
  request: ApiRequest;
  mode: Mode;
  instruments: Instrument[];
  onUseLength: (length: number) => void;
}) {
  const [settings, setSettings] = useState(CALIBRATION_DEFAULTS);
  const [basis, setBasis] = useState<CalibrationRequest["return_basis"] | null>(
    null,
  );
  const [preview, setPreview] = useState<{
    data: CalibrationPreview;
    key: string;
  }>();
  const [live, setLive] = useState<RecordedResult & { key: string }>();
  const [liveHoldout, setLiveHoldout] = useState<RecordedResult>();
  const [demo, setDemo] = useState<CalibrationDemo>();
  const [demoHoldout, setDemoHoldout] = useState(false);
  const [busy, setBusy] = useState<Operation>();
  const [progress, setProgress] = useState<Job<CalibrationResult>>();
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const body: CalibrationRequest = {
    ...settings,
    instrument_ids: request.instrument_ids,
    return_basis: basis ?? request.return_basis ?? "nominal",
  };
  const key = JSON.stringify(body);
  const currentPreview = preview?.key === key ? preview.data : undefined;

  useEffect(() => {
    if (mode !== "demo") return;
    const signal = new AbortController();
    void requestJson<CalibrationDemo>(
      "/demo/bootstrap-calibration.json",
      {},
      signal.signal,
    )
      .then(setDemo)
      .catch((err: Error) => {
        if (!signal.signal.aborted) setError(err.message);
      });
    return () => signal.abort();
  }, [mode]);

  async function loadPreview() {
    setError("");
    setBusy("preview");
    try {
      setPreview({ key, data: await previewCalibration(body) });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(undefined);
    }
  }

  async function run(holdout = false) {
    if (holdout && !live?.jobId) return;
    setError("");
    setBusy(holdout ? "holdout" : "calibration");
    setProgress(undefined);
    const abort = new AbortController();
    controller.current = abort;
    let jobId: string | undefined;
    try {
      const result = await runCalibration(
        holdout ? { calibration_job_id: live!.jobId! } : body,
        (job) => {
          jobId = job.job_id;
          setProgress(job);
        },
        abort.signal,
      );
      if (holdout)
        setLiveHoldout({
          result: rememberedHoldout(result, live!.result),
          jobId,
        });
      else {
        setLive({ result, jobId, key });
        setLiveHoldout(undefined);
      }
    } catch (err) {
      if (!abort.signal.aborted) setError((err as Error).message);
    } finally {
      setBusy(undefined);
      setProgress(undefined);
      setCancelling(false);
      controller.current = null;
    }
  }

  async function cancel() {
    if (!progress) return;
    setCancelling(true);
    try {
      const stopped = await cancelCalibration(progress.job_id);
      if (stopped.status === "cancelled") controller.current?.abort();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setCancelling(false);
    }
  }

  const record: RecordedResult | undefined =
    mode === "demo" && demo
      ? { result: demo.calibration }
      : mode === "live"
        ? live
        : undefined;
  const holdoutRecord =
    mode === "demo" && demo && demoHoldout
      ? { result: demo.holdout }
      : mode === "live"
        ? liveHoldout
        : undefined;
  const stale = mode === "live" && !!live && key !== live.key;
  const tickers = new Map(instruments.map((asset) => [asset.id, asset.ticker]));
  return (
    <section className="card">
      <h2>Bootstrap Block Length Calibration</h2>
      <p className="hint">
        Каждый прогноз строится из Train и оценивается на последующих реальных
        доходностях. Меньший Score означает лучший вероятностный прогноз.
      </p>
      {mode === "demo" ? (
        <p className="hint">
          Demo использует сохранённые расчёты Nominal USD по историческим рядам
          QCross. Детальная выборка доступна для одного Fold, горизонта и актива
          в каждом расчёте.
        </p>
      ) : (
        <div className="calibration-section">
          <p className="hint">
            Активы из общей панели выбора ·{" "}
            {body.instrument_ids
              .map((id) => tickers.get(id) ?? id)
              .join(", ") || "выберите хотя бы два актива"}
            .
          </p>
          <fieldset disabled={!!busy} className="calibration-fields">
            <legend>Настройки калибровки</legend>
            <div className="calibration-grid">
              <label>
                Начальная историческая дата
                <input
                  type="date"
                  value={settings.start_date ?? ""}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      start_date: e.target.value || null,
                    })
                  }
                />
                {!settings.start_date && <small>Самая ранняя общая дата</small>}
              </label>
              <label>
                Конечная историческая дата
                <input
                  type="date"
                  value={settings.end_date ?? ""}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      end_date: e.target.value || null,
                    })
                  }
                />
                {!settings.end_date && <small>Последняя общая дата</small>}
              </label>
              <label>
                Доходность
                <select
                  value={body.return_basis}
                  onChange={(e) =>
                    setBasis(
                      e.target.value as CalibrationRequest["return_basis"],
                    )
                  }
                >
                  <option value="nominal">Nominal USD</option>
                  <option value="real">Real USD</option>
                </select>
              </label>
              {fields.map(({ key: name, label, min, max }) => (
                <label key={name}>
                  {label}
                  <input
                    type="number"
                    min={min}
                    max={max}
                    value={settings[name] ?? ""}
                    placeholder={
                      name === "step" ? String(settings.test_length) : undefined
                    }
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        [name]:
                          name === "step" && !e.target.value
                            ? null
                            : Number(e.target.value),
                      })
                    }
                  />
                </label>
              ))}
              <label>
                Практический допуск, %
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={0.1}
                  value={Number(
                    (settings.practical_tolerance * 100).toFixed(4),
                  )}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      practical_tolerance: Number(e.target.value) / 100,
                    })
                  }
                />
              </label>
            </div>
            <div className="calibration-checks">
              <label>
                <input
                  type="checkbox"
                  checked={settings.final_holdout}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      final_holdout: e.target.checked,
                    })
                  }
                />{" "}
                Final Holdout
              </label>
            </div>
            <fieldset className="calibration-checks">
              <legend>Инвестиционные горизонты</legend>
              {CALIBRATION_HORIZONS.map((h) => (
                <label key={h}>
                  <input
                    type="checkbox"
                    checked={settings.horizons.includes(h)}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        horizons: e.target.checked
                          ? [...settings.horizons, h].sort((a, b) => a - b)
                          : settings.horizons.filter((item) => item !== h),
                      })
                    }
                  />{" "}
                  {horizonLabel(h)}
                </label>
              ))}
            </fieldset>
          </fieldset>
          <div className="calibration-actions">
            <button
              disabled={
                !!busy ||
                body.instrument_ids.length < 2 ||
                !body.horizons.length
              }
              onClick={() => void loadPreview()}
            >
              Проверить периоды
            </button>
            <button
              className="primary"
              disabled={!!busy || !currentPreview?.ready}
              onClick={() => void run()}
            >
              Запустить Calibration
            </button>
            {busy === "preview" && (
              <span role="status">Проверяем общую историю</span>
            )}
          </div>
          {busy === "calibration" && (
            <CalibrationProgress
              name="Calibration"
              job={progress}
              cancelling={cancelling}
              onCancel={() => void cancel()}
            />
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="warning">
          {error}
        </p>
      )}
      {(mode === "demo" ? demo?.calibration.preview : currentPreview) && (
        <Preview
          preview={
            (mode === "demo" ? demo?.calibration.preview : currentPreview)!
          }
        />
      )}
      {stale && (
        <p className="warning">
          Показан сохранённый результат. Для новых настроек выполните
          Calibration снова.
        </p>
      )}
      {record && (
        <>
          <CalibrationResults
            key={record.jobId ?? "demo"}
            {...record}
            demoDetail={mode === "demo" ? demo?.calibration_detail : undefined}
            onUseLength={stale ? undefined : onUseLength}
          />
          {record.result.holdout_status === "available" && !holdoutRecord && (
            <div className="calibration-actions">
              <button
                disabled={!!busy || stale}
                onClick={() =>
                  mode === "demo" ? setDemoHoldout(true) : void run(true)
                }
              >
                {mode === "demo"
                  ? "Показать сохранённый Final Holdout"
                  : "Запустить Final Holdout"}
              </button>
            </div>
          )}
          {mode === "live" && busy === "holdout" && (
            <CalibrationProgress
              name="Final Holdout"
              job={progress}
              cancelling={cancelling}
              onCancel={() => void cancel()}
            />
          )}
        </>
      )}
      {holdoutRecord && (
        <>
          <h3>Final Holdout</h3>
          <p className="hint">
            Проверяется L={holdoutRecord.result.optimal_length}, выбранное на
            Calibration. Показатели Holdout отсутствуют в рейтинге.
          </p>
          {holdoutRecord.result.holdout_status === "previously_viewed" && (
            <p className="warning">
              Этот период уже использовался для Final Holdout. После изменения
              методологии он больше не считается нетронутой проверкой.
            </p>
          )}
          <CalibrationResults
            key={holdoutRecord.jobId ?? "demo-holdout"}
            {...holdoutRecord}
            demoDetail={mode === "demo" ? demo?.holdout_detail : undefined}
          />
        </>
      )}
      <details className="calibration-section">
        <summary>Методология и ограничения</summary>
        <ul>
          <li>
            Stationary Bootstrap использует историческое распределение. Смена
            рыночных режимов, ошибки реконструкции и циклические переходы между
            концом и началом Train могут влиять на результат.
          </li>
          <li>
            Непересекающиеся тестовые интервалы, разные горизонты и активы могут
            оставаться зависимыми. Число реальных двадцатилетних проверок
            ограничено. Bootstrap-генерации не добавляют независимых
            исторических экспериментов.
          </li>
          <li>
            Рейтинг зависит от состава активов, характеристик и весов.
            Практический допуск показывает близость Score без оценки
            статистической значимости.
          </li>
          <li>
            Выбранное L калибрует генератор сценариев. Точность будущего
            прогноза и будущая доходность остаются неопределёнными.
          </li>
        </ul>
      </details>
    </section>
  );
}
