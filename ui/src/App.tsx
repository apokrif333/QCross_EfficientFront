import { useEffect, useRef, useState } from "react";
import type {
  ApiRequest,
  BootstrapResult,
  Calculation,
  Catalog,
  CVResult,
  DemoBundle,
  FrontierResult,
  Job,
  Kind,
  Mode,
  ResampledResult,
} from "./types";
import { DEFAULT_REQUEST, METHODS } from "./types";
import { fetchCatalog, requestJson, runJob } from "./lib/api";
import {
  compareCalculations,
  compatible,
  createSnapshot,
  downloadJson,
  parseSnapshot,
  type Comparison,
} from "./lib/snapshot";
import Settings from "./components/Settings";
import AssetBuilder from "./components/AssetBuilder";
import {
  allocationError,
  requestFromRows,
  rowsFromRequest,
  type AssetRow,
} from "./lib/allocations";
import {
  BootstrapView,
  CVView,
  FrontierView,
  ResampledView,
} from "./components/Results";
import { number } from "./lib/display";

const TABS = [
  "Frontier",
  "Cross-validation",
  "Bootstrap",
  "Compare",
  "Snapshot",
] as const;
type Tab = (typeof TABS)[number];
async function loadDemo(): Promise<DemoBundle> {
  const manifest = await requestJson<{
    catalog: Catalog;
    records: (Omit<Calculation, "result" | "created_at"> & { file: string })[];
  }>("/demo/manifest.json");
  const calculations = await Promise.all(
    manifest.records.map(async (record) => ({
      ...record,
      result: await requestJson<Calculation["result"]>(`/demo/${record.file}`),
      created_at: "saved-demo",
    })),
  );
  return { catalog: manifest.catalog, calculations };
}
export default function App() {
  const [mode, setMode] = useState<Mode>("demo"),
    [tab, setTab] = useState<Tab>("Frontier");
  const [catalog, setCatalog] = useState<Catalog | null>(null),
    [demo, setDemo] = useState<DemoBundle | null>(null);
  const [request, setRequest] = useState<ApiRequest>(DEFAULT_REQUEST),
    [calculations, setCalculations] = useState<Calculation[]>([]);
  const [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(""),
    [progress, setProgress] = useState<Job | null>(null);
  const [baseline, setBaseline] = useState<Calculation[]>([]),
    [comparisons, setComparisons] = useState<
      { label: string; comparison: Comparison }[]
    >([]);
  const [absTol, setAbsTol] = useState(1e-7),
    [relTol, setRelTol] = useState(1e-6),
    [loaded, setLoaded] = useState(false);
  const [periodInfo, setPeriodInfo] = useState("");
  const [assetRows, setAssetRows] = useState<AssetRow[]>(() =>
    rowsFromRequest(DEFAULT_REQUEST),
  );
  const formError = allocationError(assetRows);
  const adoptRequest = (next: ApiRequest) => {
    setRequest(next);
    setAssetRows(rowsFromRequest(next));
  };
  const updateRows = (rows: AssetRow[]) => {
    setAssetRows(rows);
    setRequest((previous) => requestFromRows(rows, previous));
  };
  const controller = useRef<AbortController | null>(null),
    file = useRef<HTMLInputElement>(null);
  const instruments = catalog?.instruments ?? [];
  const periodKey = JSON.stringify([
    request.instrument_ids,
    request.start_date,
    request.end_date,
  ]);
  useEffect(() => {
    setPeriodInfo("");
    if (mode !== "live" || request.instrument_ids.length < 2) return;
    const abort = new AbortController();
    const timer = setTimeout(() => {
      void requestJson<FrontierResult>(
        "/api/v1/analytics/data-preview",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            instrument_ids: request.instrument_ids,
            currency: "USD",
            start_date: request.start_date,
            end_date: request.end_date,
          }),
        },
        abort.signal,
      )
        .then((p) =>
          setPeriodInfo(
            `Выборка: ${p.period.start} — ${p.period.end} · ${p.period.observations} месяцев`,
          ),
        )
        .catch((e) => {
          if (!abort.signal.aborted)
            setPeriodInfo(`Выборка не подтверждена: ${e.message}`);
        });
    }, 600);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
    // Other estimation settings do not alter the aligned observations.
  }, [mode, periodKey]);
  useEffect(() => {
    let active = true;
    loadDemo()
      .then((bundle) => {
        if (!active) return;
        setDemo(bundle);
        setCatalog(bundle.catalog);
        setCalculations(bundle.calculations);
        adoptRequest(bundle.calculations[0].request);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
      controller.current?.abort();
    };
  }, []);
  const records = calculations.filter(
    (c) => c.request.covariance_method === request.covariance_method,
  );
  const frontier = records.find((c) => c.kind === "frontier"),
    cv = records.find((c) => c.kind === "cross-validation"),
    bootstrap = records.find((c) => c.kind === "bootstrap"),
    resampled = records.find((c) => c.kind === "resampled-frontier");
  const visible =
    tab === "Cross-validation"
      ? cv
      : tab === "Bootstrap"
        ? (bootstrap ?? resampled)
        : frontier;
  async function changeMode(next: Mode) {
    setError("");
    setMessage("");
    setBusy("Загрузка каталога");
    try {
      if (next === "demo") {
        if (!demo) throw new Error("Demo ещё не загружен.");
        setCatalog(demo.catalog);
        setCalculations(demo.calculations);
        adoptRequest(demo.calculations[0].request);
        setLoaded(false);
        setBaseline([]);
        setComparisons([]);
      } else {
        const c = await fetchCatalog();
        setCatalog(c);
        if (!loaded && mode === "demo") {
          const next = {
            ...request,
            asset_constraints: {},
            group_constraints: {},
            asset_groups: {},
            user_weights: null,
            start_date: null,
            end_date: null,
            bootstrap_objectives: [
              "gmv",
              "max_sharpe",
            ] as ApiRequest["bootstrap_objectives"],
          };
          adoptRequest(requestFromRows(rowsFromRequest(next), next));
        }
        if (mode !== "live" && !loaded) setCalculations([]);
      }
      setMode(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  function changeRequest(next: ApiRequest) {
    if (mode === "demo") {
      const found = (loaded ? calculations : demo?.calculations)?.find(
        (c) => c.request.covariance_method === next.covariance_method,
      );
      if (found) adoptRequest(found.request);
    } else setRequest(next);
  }
  async function preview() {
    setError("");
    setBusy("Проверка общей выборки");
    try {
      const p = await requestJson<FrontierResult>(
        "/api/v1/analytics/data-preview",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            instrument_ids: request.instrument_ids,
            currency: "USD",
            start_date: request.start_date,
            end_date: request.end_date,
          }),
        },
      );
      setMessage(
        `Период ${p.period.start} — ${p.period.end}, ${p.period.observations} месяцев. ${p.warnings.join(" ")}`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function execute(
    kind: Kind,
    body: ApiRequest,
    signal: AbortSignal,
  ): Promise<Calculation> {
    const result = await runJob(kind, body, setProgress, signal);
    const calculation: Calculation = {
      id: crypto.randomUUID(),
      kind,
      request: structuredClone(result.reproducibility.api_request ?? body),
      result,
      origin: "live",
      created_at: new Date().toISOString(),
    };
    setCalculations((prev) => [
      calculation,
      ...prev.filter(
        (c) =>
          !(
            c.kind === kind &&
            c.request.covariance_method === body.covariance_method
          ),
      ),
    ]);
    return calculation;
  }
  async function calculate(kinds: Kind[], compare = false) {
    if (formError) {
      setError(formError);
      return;
    }
    setError("");
    setMessage("");
    setLoaded(false);
    setProgress(null);
    const abort = new AbortController();
    controller.current = abort;
    setBusy(compare ? "Сравнение четырёх методов" : "Расчёт");
    try {
      const bodies = compare
        ? METHODS.map((m) => ({ ...request, covariance_method: m.value }))
        : [structuredClone(request)];
      for (const body of bodies)
        for (const kind of kinds) {
          setBusy(
            `${kind} · ${METHODS.find((m) => m.value === body.covariance_method)!.short}`,
          );
          const actualBody =
            kind === "bootstrap"
              ? {
                  ...body,
                  bootstrap_objectives: [
                    "gmv",
                    "max_sharpe",
                  ] as ApiRequest["bootstrap_objectives"],
                }
              : body;
          const c = await execute(kind, actualBody, abort.signal);
          if (c.result.status !== "complete")
            setMessage(
              "Получен неполный результат. Смотрите статусы, причины ошибок и численные диагностики.",
            );
        }
    } catch (e) {
      setError(
        abort.signal.aborted
          ? "Ожидание отменено. Задание на сервере может продолжать выполняться."
          : (e as Error).message,
      );
    } finally {
      setBusy("");
      setProgress(null);
      controller.current = null;
    }
  }
  async function restore(f: File) {
    setError("");
    try {
      if (f.size > 80 * 1024 * 1024)
        throw new Error("Максимальный размер снимка — 80 MB.");
      const s = parseSnapshot(await f.text());
      setCalculations(s.calculations);
      setBaseline(s.calculations);
      setCatalog({
        engine: s.calculations[0].result.reproducibility.engine,
        instruments: s.instruments,
      });
      adoptRequest(s.calculations[0].request);
      setLoaded(true);
      setComparisons([]);
      setTab("Snapshot");
      setMessage(
        "Снимок загружен локально. Для повторения будут отправлены сохранённые запросы в Live API.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      if (file.current) file.current.value = "";
    }
  }
  async function repeat() {
    const originals = baseline.length ? baseline : calculations;
    const abort = new AbortController();
    controller.current = abort;
    setBusy("Повторение снимка");
    setError("");
    setComparisons([]);
    try {
      if (
        !Number.isFinite(absTol) ||
        absTol < 0 ||
        !Number.isFinite(relTol) ||
        relTol < 0
      )
        throw new Error(
          "Допуски должны быть конечными неотрицательными числами.",
        );
      const c = await fetchCatalog(abort.signal);
      setCatalog(c);
      setMode("live");
      setBaseline(originals);
      for (const original of originals) {
        setBusy(
          `Повторение ${original.kind} · ${original.request.covariance_method}`,
        );
        const next = await execute(
          original.kind,
          original.request,
          abort.signal,
        );
        setComparisons((prev) => [
          ...prev,
          {
            label: `${original.kind} / ${original.request.covariance_method}`,
            comparison: compareCalculations(original, next, absTol, relTol),
          },
        ]);
      }
      setLoaded(false);
    } catch (e) {
      setError(
        abort.signal.aborted
          ? "Повторение прервано; незавершённые результаты не подтверждены."
          : (e as Error).message,
      );
    } finally {
      setBusy("");
      setProgress(null);
      controller.current = null;
    }
  }
  const stale =
    visible &&
    mode === "live" &&
    JSON.stringify(visible.request) !== JSON.stringify(request);
  const resultLabel = loaded
    ? "JSON Snapshot"
    : mode === "demo"
      ? "DEMO · сохранённые данные"
      : "LIVE · FastAPI";
  return (
    <>
      <header className="topbar">
        <a className="brand" href="#">
          <span className="brand-symbol">Q</span>
          <span>
            QCROSS<small>PORTFOLIO LAB</small>
          </span>
        </a>
        <div className="top-actions">
          <span className="badge">USD ONLY</span>
          <button
            className="secondary"
            disabled={!!busy || !calculations.length}
            onClick={() =>
              downloadJson(
                "qcross-portfolio-snapshot.json",
                createSnapshot(calculations, instruments),
              )
            }
          >
            Сохранить JSON
          </button>
          <button
            className="secondary"
            disabled={!!busy}
            onClick={() => file.current?.click()}
          >
            Загрузить JSON
          </button>
          <input
            ref={file}
            type="file"
            accept=".json,application/json"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void restore(f);
            }}
          />
        </div>
      </header>
      <div className="intro">
        <div>
          <div className="eyebrow">RESEARCH WORKSPACE</div>
          <h1>Portfolio Lab</h1>
          <p>
            Оптимизация портфеля, проверка устойчивости и воспроизводимые
            результаты.
          </p>
        </div>
        <div className="mode-control">
          <div className="segmented">
            <button
              className={mode === "demo" ? "active" : ""}
              disabled={!!busy}
              onClick={() => void changeMode("demo")}
            >
              Demo
            </button>
            <button
              className={mode === "live" ? "active" : ""}
              disabled={!!busy}
              onClick={() => void changeMode("live")}
            >
              Live
            </button>
          </div>
          <small>
            Engine {catalog?.engine.version ?? "…"} · {resultLabel}
          </small>
        </div>
      </div>
      {error && (
        <div className="notice error" role="alert">
          <b>Расчёт не подтверждён.</b> {error}
        </div>
      )}
      {message && (
        <div className="notice" role="status">
          {message}
        </div>
      )}
      {busy && (
        <div className="notice working" role="status">
          <span className="spinner" />
          {busy}
          {progress && (
            <span>
              {progress.status} · {progress.elapsed_seconds.toFixed(1)} с ·
              лимит {progress.timeout_seconds} с
            </span>
          )}
          {controller.current && (
            <button
              className="text-button"
              onClick={() => controller.current?.abort()}
            >
              Отменить ожидание
            </button>
          )}
        </div>
      )}
      <AssetBuilder
        rows={assetRows}
        instruments={instruments}
        locked={!!busy || mode === "demo"}
        onChange={updateRows}
      />
      <div className="workspace">
        <aside>
          <details className="settings-shell" open>
            <summary>Настройки расчёта</summary>
            {mode === "demo" && !loaded && (
              <p className="demo-note">
                Demo показывает реальные сохранённые расчёты VTI / TLT / GLD.
                Новые параметры и активы доступны в Live.
              </p>
            )}
            {periodInfo && (
              <p className="demo-note" role="status">
                {periodInfo}
              </p>
            )}
            <Settings
              value={request}
              instruments={instruments}
              locked={!!busy || mode === "demo"}
              onChange={changeRequest}
              onPreview={() => void preview()}
            />
            {mode === "demo" && (
              <label className="demo-method">
                {loaded ? "Метод в снимке" : "Метод в Demo"}
                <select
                  value={request.covariance_method}
                  onChange={(e) =>
                    changeRequest({
                      ...request,
                      covariance_method: e.target
                        .value as ApiRequest["covariance_method"],
                    })
                  }
                >
                  {METHODS.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </details>
        </aside>
        <main>
          <nav className="tabs" aria-label="Разделы анализа">
            {TABS.map((t) => (
              <button
                key={t}
                aria-current={tab === t ? "page" : undefined}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </nav>
          <section className="card">
            <div className="section-heading">
              <div>
                <h2>
                  {tab === "Frontier"
                    ? "Efficient Frontier"
                    : tab === "Cross-validation"
                      ? "Classical K-Fold Cross-Validation"
                      : tab === "Bootstrap"
                        ? "Stationary Bootstrap"
                        : tab === "Compare"
                          ? "Сравнение методов"
                          : "Воспроизводимость"}
                </h2>
                <p>
                  {visible
                    ? `${visible.result.period.start} — ${visible.result.period.end} · ${visible.result.period.observations} месяцев · ${visible.request.covariance_method}`
                    : "Выберите активы и запустите расчёт"}
                </p>
              </div>
              {mode === "live" && tab !== "Snapshot" && (
                <button
                  className="primary"
                  disabled={
                    !!busy || !!formError || request.instrument_ids.length < 2
                  }
                  onClick={() =>
                    void calculate(
                      [
                        tab === "Cross-validation"
                          ? "cross-validation"
                          : tab === "Bootstrap"
                            ? "bootstrap"
                            : "frontier",
                      ],
                      tab === "Compare",
                    )
                  }
                >
                  {tab === "Compare" ? "Сравнить 4 метода" : "Рассчитать"}
                </button>
              )}
            </div>
            {stale && (
              <p className="warning">
                Показан ранее полученный результат. Текущие настройки
                отличаются; выполните новый расчёт.
              </p>
            )}
            {visible?.result.status === "incomplete" && (
              <p className="warning">
                INCOMPLETE: расчёт завершён частично. Такой результат не принят
                как полный.
              </p>
            )}
            {tab === "Frontier" && frontier && (
              <FrontierView
                key={frontier.id}
                calculation={frontier}
                related={calculations}
                instruments={instruments}
              />
            )}
            {tab === "Compare" && frontier && (
              <FrontierView
                key={`${frontier.id}-compare`}
                calculation={frontier}
                related={calculations}
                instruments={instruments}
                compare
              />
            )}
            {tab === "Cross-validation" && cv && (
              <CVView
                result={cv.result as CVResult}
                instruments={instruments}
              />
            )}
            {tab === "Bootstrap" && (
              <>
                {bootstrap && (
                  <BootstrapView
                    result={bootstrap.result as BootstrapResult}
                    resampled={
                      resampled && compatible(bootstrap, resampled)
                        ? (resampled.result as ResampledResult)
                        : undefined
                    }
                    instruments={instruments}
                  />
                )}
                {!bootstrap && resampled && (
                  <ResampledView
                    result={resampled.result as ResampledResult}
                    instruments={instruments}
                  />
                )}
                <div className="subsection">
                  <h3>Optional Resampled Frontier</h3>
                  <p className="hint">
                    Общие risk-aversion параметры для всех выборок. Это
                    отдельная кривая, не точная классическая граница. Число
                    повторений берётся из настроек (максимум 500).
                  </p>
                  {mode === "live" && (
                    <button
                      className="secondary"
                      disabled={
                        !!busy ||
                        !!formError ||
                        request.instrument_ids.length < 2
                      }
                      onClick={() => void calculate(["resampled-frontier"])}
                    >
                      Рассчитать Resampled Frontier
                    </button>
                  )}
                </div>
              </>
            )}
            {tab !== "Snapshot" && !visible && (
              <div className="empty">
                <span>↗</span>
                <h3>Расчёт ещё не выполнен</h3>
                <p>
                  Результаты появятся здесь после ответа API. В Demo доступны
                  сохранённые сценарии.
                </p>
              </div>
            )}
            {tab === "Snapshot" && (
              <>
                <p className="hint">
                  JSON содержит полные запросы, ответы, версии движка и рядов,
                  SHA-256 матрицы, seed и параметры оптимизации. Загрузка файла
                  не выполняет расчёт.
                </p>
                <div className="two tolerances">
                  <label>
                    Абсолютный допуск
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={absTol}
                      onChange={(e) => setAbsTol(+e.target.value)}
                    />
                  </label>
                  <label>
                    Относительный допуск
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={relTol}
                      onChange={(e) => setRelTol(+e.target.value)}
                    />
                  </label>
                </div>
                <p className="hint">
                  Проверка: |new − saved| ≤ abs + rel × |saved|. Время
                  выполнения и счётчики итераций solver исключены;
                  bootstrap-итерации проверяются. Повторение всех сохранённых
                  заданий может занять несколько минут.
                </p>
                <button
                  className="primary"
                  disabled={!!busy || !calculations.length}
                  onClick={() => void repeat()}
                >
                  Повторить снимок через Live
                </button>
                {comparisons.map(({ label, comparison: c }) => (
                  <div className="subsection" key={label}>
                    <h3>{label}</h3>
                    <p className={c.differences.length ? "warning" : "success"}>
                      {c.differences.length
                        ? `${c.differences.length} различий за пределами допусков`
                        : "Численные результаты совпадают в пределах допусков"}{" "}
                      · проверено {c.numericFields} полей · max Δ{" "}
                      {c.maxAbsoluteDifference.toExponential(3)}
                    </p>
                    {c.warnings.map((w) => (
                      <p className="warning" key={w}>
                        {w}
                      </p>
                    ))}
                    {c.differences.length > 0 && (
                      <>
                        <div className="table-scroll">
                          <table>
                            <thead>
                              <tr>
                                <th>Поле</th>
                                <th>Saved</th>
                                <th>New</th>
                                <th>Δ / допуск</th>
                              </tr>
                            </thead>
                            <tbody>
                              {c.differences.slice(0, 100).map((d) => (
                                <tr key={d.path}>
                                  <td>{d.path}</td>
                                  <td>{JSON.stringify(d.before)}</td>
                                  <td>{JSON.stringify(d.after)}</td>
                                  <td>
                                    {d.difference} / {d.tolerance}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <button
                          className="text-button"
                          onClick={() =>
                            downloadJson("qcross-differences.json", c)
                          }
                        >
                          Скачать все различия JSON
                        </button>
                      </>
                    )}
                  </div>
                ))}
                <h3>Сохранённые расчёты ({calculations.length})</h3>
                {calculations.map((c) => (
                  <details className="provenance" key={c.id}>
                    <summary>
                      {c.kind} · {c.request.covariance_method} ·{" "}
                      {c.result.status} · seed {c.request.random_seed}
                    </summary>
                    <p>
                      Engine {c.result.reproducibility.engine.version} · source{" "}
                      {c.result.reproducibility.engine.source_sha256}
                    </p>
                    <p>
                      Matrix SHA-256:{" "}
                      <code>
                        {c.result.reproducibility.returns_matrix_sha256}
                      </code>
                    </p>
                    <pre>
                      {JSON.stringify(
                        {
                          request: c.request,
                          provenance: c.result.reproducibility,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                ))}
              </>
            )}
          </section>
          {visible && tab !== "Snapshot" && (
            <section className="card diagnostics">
              <h3>Диагностика и источники</h3>
              <p>
                Время численного расчёта:{" "}
                {number(visible.result.execution_seconds)} с ·{" "}
                {visible.origin.toUpperCase()} · {visible.result.status}
              </p>
              {visible.result.warnings.map((w, i) => (
                <p className="warning" key={i}>
                  {w}
                </p>
              ))}
              <details>
                <summary>
                  Covariance parameters / numerical diagnostics / SHA-256
                </summary>
                <pre>
                  {JSON.stringify(
                    {
                      parameters: visible.result.covariance_parameters,
                      diagnostics: visible.result.covariance_diagnostics,
                      provenance: visible.result.reproducibility,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </section>
          )}
          <footer>
            QCross Portfolio Lab · monthly total returns · USD · long-only ·
            monthly rebalancing
          </footer>
        </main>
      </div>
    </>
  );
}
