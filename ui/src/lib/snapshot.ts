import type { ApiRequest, Calculation, Instrument, Snapshot } from "../types";
import { instrumentSchema, validateRecord } from "./validation";
export function stable(value: unknown): string {
  const normalize = (item: unknown): unknown =>
    Array.isArray(item)
      ? item.map(normalize)
      : item && typeof item === "object"
        ? Object.fromEntries(
            Object.entries(item)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([k, v]) => [k, normalize(v)]),
          )
        : item;
  return JSON.stringify(normalize(value));
}
export function settingsKey(request: ApiRequest): string {
  const {
    covariance_method: _method,
    bootstrap_iterations: _iterations,
    bootstrap_objectives: _objectives,
    risk_aversions: _aversions,
    ...rest
  } = request;
  return stable(rest);
}
export function compatible(a: Calculation, b: Calculation): boolean {
  return (
    settingsKey(a.request) === settingsKey(b.request) &&
    a.result.reproducibility.returns_matrix_sha256 ===
      b.result.reproducibility.returns_matrix_sha256
  );
}
export function createSnapshot(
  calculations: Calculation[],
  instruments: Instrument[],
): Snapshot {
  return {
    schema: "qcross-portfolio-lab/v1",
    created_at: new Date().toISOString(),
    instruments,
    calculations,
  };
}
export function parseSnapshot(text: string): Snapshot {
  if (text.length > 80 * 1024 * 1024)
    throw new Error("Максимальный размер снимка — 80 MB.");
  const value = JSON.parse(text);
  if (
    value.schema !== "qcross-portfolio-lab/v1" ||
    !Array.isArray(value.calculations) ||
    !value.calculations.length ||
    !Array.isArray(value.instruments)
  )
    throw new Error("Это не полный снимок QCross Portfolio Lab v1.");
  if (text.length > 80 * 1024 * 1024 || value.calculations.length > 32)
    throw new Error("Снимок превышает допустимый размер: 80 MB / 32 расчёта.");
  for (const record of value.calculations) {
    try {
      validateRecord(record);
    } catch {
      throw new Error(
        "Снимок повреждён: неверный запрос, результат или provenance.",
      );
    }
    const provenance = record.result?.reproducibility;
    if (
      ![
        "frontier",
        "cross-validation",
        "bootstrap",
        "resampled-frontier",
      ].includes(record.kind) ||
      !record.request ||
      !Array.isArray(record.request.instrument_ids) ||
      !provenance?.engine?.version ||
      !/^[a-f0-9]{64}$/.test(provenance.returns_matrix_sha256) ||
      !Array.isArray(provenance.source_series) ||
      !["complete", "incomplete"].includes(record.result.status)
    )
      throw new Error(
        "Снимок повреждён: отсутствуют запрос, результат или сведения о воспроизводимости.",
      );
  }
  for (const instrument of value.instruments)
    instrumentSchema.parse(instrument);
  for (const record of value.calculations)
    if (
      record.request.instrument_ids.some(
        (id: number) => !value.instruments.some((i: Instrument) => i.id === id),
      )
    )
      throw new Error("В снимке отсутствует каталог выбранных инструментов.");
  return value as Snapshot;
}
export interface Difference {
  path: string;
  before: unknown;
  after: unknown;
  difference?: number;
  tolerance?: number;
}
export interface Comparison {
  differences: Difference[];
  warnings: string[];
  numericFields: number;
  maxAbsoluteDifference: number;
}
const TIMING =
  /(?:^|\.)(execution_seconds|wall_seconds|solve_seconds|solve_time|elapsed_seconds|submitted_at|created_at|last_updated_at)$|\.diagnostics\.iterations$/;
export function compareCalculations(
  before: Calculation,
  after: Calculation,
  absoluteTolerance: number,
  relativeTolerance: number,
): Comparison {
  if (
    ![absoluteTolerance, relativeTolerance].every(
      (n) => Number.isFinite(n) && n >= 0,
    )
  )
    throw new Error("Допуски должны быть конечными неотрицательными числами.");
  const differences: Difference[] = [],
    warnings: string[] = [];
  let numericFields = 0,
    maxAbsoluteDifference = 0;
  const walk = (a: unknown, b: unknown, path: string) => {
    if (TIMING.test(path)) return;
    if (typeof a === "number" && typeof b === "number") {
      numericFields++;
      const difference = Math.abs(a - b),
        tolerance = absoluteTolerance + relativeTolerance * Math.abs(a);
      maxAbsoluteDifference = Math.max(maxAbsoluteDifference, difference);
      if (!Number.isFinite(a) || !Number.isFinite(b) || difference > tolerance)
        differences.push({ path, before: a, after: b, difference, tolerance });
    } else if (
      a &&
      b &&
      typeof a === "object" &&
      typeof b === "object" &&
      Array.isArray(a) === Array.isArray(b)
    ) {
      for (const key of new Set([...Object.keys(a), ...Object.keys(b)]))
        walk(
          (a as Record<string, unknown>)[key],
          (b as Record<string, unknown>)[key],
          path ? `${path}.${key}` : key,
        );
    } else if (a !== b) differences.push({ path, before: a, after: b });
  };
  const pa = before.result.reproducibility,
    pb = after.result.reproducibility;
  if (stable(pa.engine) !== stable(pb.engine))
    warnings.push(
      "Версия движка, исходного кода или численных библиотек изменилась.",
    );
  if (pa.returns_matrix_sha256 !== pb.returns_matrix_sha256)
    warnings.push("SHA-256 использованной матрицы доходностей изменился.");
  if (stable(pa.source_series) !== stable(pb.source_series))
    warnings.push(
      "Идентификаторы, версии или даты обновления исходных рядов отличаются.",
    );
  if (stable(before.request) !== stable(after.request))
    warnings.push("Полные API-запросы отличаются.");
  if (before.kind !== after.kind)
    warnings.push("Сравниваются разные виды задания.");
  // Provenance is compared explicitly above. Runtime and solver iteration counts
  // are observational diagnostics, not numerical portfolio reproducibility.
  const { reproducibility: _pa, ...a } = before.result;
  const { reproducibility: _pb, ...b } = after.result;
  walk(a, b, "result");
  return { differences, warnings, numericFields, maxAbsoluteDifference };
}
export function downloadJson(filename: string, value: unknown): void {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
