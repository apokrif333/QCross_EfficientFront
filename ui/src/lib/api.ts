import type {
  ApiRequest,
  AssetTrendSensitivitySeries,
  BootstrapControlCache,
  BootstrapTrendSensitivity,
  Catalog,
  Job,
  Kind,
  Provenance,
  Result,
} from "../types";
import { hasCompleteAssetTrendMetrics, requestSchemaFor } from "./validation";
import type {
  CalibrationDetail,
  CalibrationPreview,
  CalibrationRequest,
  CalibrationResult,
} from "./calibration";
const BASE = import.meta.env.VITE_API_BASE_URL || "";
export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const timeout = AbortSignal.timeout(15000);
  try {
    const response = await fetch(`${BASE}${path}`, {
      ...init,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    const text = await response.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      throw new ApiError(
        `Ответ API не является JSON (HTTP ${response.status}).`,
        response.status,
      );
    }
    if (!response.ok) {
      const detail = body.detail;
      throw new ApiError(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((d: { msg: string }) => d.msg).join("; ")
            : `Ошибка API: HTTP ${response.status}`,
        response.status,
      );
    }
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (signal?.aborted)
      throw new ApiError(
        "Ожидание отменено. Задание на сервере может продолжать работу.",
      );
    throw new ApiError(
      "Бэкенд недоступен или запрос превысил 15 секунд. Проверьте FastAPI на порту 8000.",
    );
  }
}
export const fetchCatalog = (signal?: AbortSignal) =>
  requestJson<Catalog>("/api/v1/analytics/catalog", {}, signal);
export async function runJob(
  kind: Kind,
  body: ApiRequest,
  onProgress: (job: Job) => void,
  signal: AbortSignal,
  controlCache?: BootstrapControlCache,
): Promise<Result> {
  body = {
    ...body,
    return_basis: body.return_basis ?? "nominal",
    risk_free_source: body.risk_free_source ?? "fixed_annual",
  };
  requestSchemaFor(kind).parse(body);
  return submitJob<Result>(
    kind,
    kind === "resampled-frontier" && controlCache
      ? { ...body, bootstrap_control_cache: controlCache }
      : body,
    onProgress,
    signal,
  );
}

export function runAssetTrendSensitivity(
  body: ApiRequest,
  referenceHash: string,
  onProgress: (job: Job<unknown>) => void,
  signal: AbortSignal,
) {
  return submitJob<{
    by_asset: Record<string, AssetTrendSensitivitySeries>;
    reproducibility: Provenance;
  }>(
    "asset-trend-sensitivity",
    {
      ...body,
      reference_panel_sha256: referenceHash,
    },
    onProgress,
    signal,
  )
    .then((result) => {
      if (
        Object.values(result.by_asset).some(
          (series) => !hasCompleteAssetTrendMetrics(series),
        )
      ) {
        throw new ApiError(
          "FastAPI вернул неполные показатели по активам. Перезапустите сервер из текущей версии проекта и повторите расчёт.",
        );
      }
      return result;
    })
    .catch((error) => {
      if (
        error instanceof ApiError &&
        error.status === 404 &&
        error.message === "Not Found"
      )
        throw new ApiError(
          "Запущенный FastAPI не поддерживает этот расчёт. Перезапустите сервер из текущей версии проекта.",
          404,
        );
      throw error;
    });
}

export function runBootstrapTrendSensitivity(
  bootstrapJobId: string,
  onProgress: (job: Job<unknown>) => void,
  signal: AbortSignal,
) {
  return submitJob<{ trend_sensitivity: BootstrapTrendSensitivity }>(
    "bootstrap-trend-sensitivity",
    { bootstrap_job_id: bootstrapJobId },
    onProgress,
    signal,
  );
}

const CALIBRATION_PATH = "/api/v1/analytics/bootstrap-calibration";

export function previewCalibration(body: CalibrationRequest) {
  return requestJson<CalibrationPreview>(CALIBRATION_PATH + "/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function runCalibration(
  body: CalibrationRequest | { calibration_job_id: string },
  onProgress: (job: Job<CalibrationResult>) => void,
  signal: AbortSignal,
) {
  return submitJob<CalibrationResult>(
    "calibration_job_id" in body
      ? "bootstrap-calibration-holdout"
      : "bootstrap-calibration",
    body,
    onProgress,
    signal,
  );
}

export function fetchCalibrationDetail(
  jobId: string,
  fold: string,
  horizon: string,
  metrics: string[],
  member: string[],
  lengths: number[],
  signal: AbortSignal,
) {
  const search = new URLSearchParams({ fold, horizon });
  metrics.forEach((metric) => search.append("metrics", metric));
  member.forEach((asset) => search.append("member", asset));
  lengths.forEach((length) => search.append("lengths", String(length)));
  return requestJson<CalibrationDetail>(
    CALIBRATION_PATH + "/" + jobId + "/detail?" + search.toString(),
    {},
    signal,
  );
}

export function cancelCalibration(jobId: string) {
  return requestJson<Job>(CALIBRATION_PATH + "/" + jobId, { method: "DELETE" });
}

async function submitJob<T>(
  kind: Job["kind"],
  body: object,
  onProgress: (job: Job<T>) => void,
  signal: AbortSignal,
): Promise<T> {
  let job = await requestJson<Job<T>>(
    `/api/v1/analytics/${kind}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    signal,
  );
  onProgress(job);
  const deadline = Date.now() + (job.timeout_seconds + 15) * 1000;
  while (job.status === "running") {
    if (Date.now() > deadline)
      throw new ApiError(
        "Превышено время ожидания задания. Проверьте его статус на сервере.",
      );
    await new Promise<void>((resolve, reject) => {
      if (signal.aborted) {
        reject(new ApiError("Ожидание отменено."));
        return;
      }
      const abort = () => {
        clearTimeout(timer);
        reject(new ApiError("Ожидание отменено."));
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve();
      }, 350);
      signal.addEventListener("abort", abort, { once: true });
    });
    job = await requestJson<Job<T>>(job.status_url, {}, signal);
    onProgress(job);
  }
  if (job.status !== "completed")
    throw new ApiError(job.error || `Задание: ${job.status}`);
  const finished = await requestJson<Job<T>>(job.result_url, {}, signal);
  if (!finished.result)
    throw new ApiError("API завершил задание без результата.");
  return finished.result;
}
