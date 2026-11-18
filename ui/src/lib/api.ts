import type {
  ApiRequest,
  AssetTrendSensitivitySeries,
  BootstrapControlCache,
  Catalog,
  Job,
  Kind,
  Provenance,
  Result,
} from "../types";
import { requestSchemaFor } from "./validation";
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
  ).catch((error) => {
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
