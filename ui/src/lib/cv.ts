import type { ApiRequest, Kind } from "../types";

export const CV_FOLDS = Array.from({ length: 10 }, (_, i) => 2 + 2 * i);
export function minimumCVHistory(k: number, covariance = false): number {
  return k === 2 ? (covariance ? 90 : 180) : k * (covariance ? 30 : 60);
}
export function cvHistoryError(
  request: ApiRequest,
  kind: Kind,
  observations?: number,
): string | null {
  if (kind === "rebalancing-optimization") return null;
  const k = request.cv_folds;
  if (!CV_FOLDS.includes(k)) return "K: выберите чётное число от 2 до 20.";
  const portfolio = kind === "cross-validation";
  if (kind === "rolling-efficiency") {
    if (!request.user_weights || Object.keys(request.user_weights).length === 0)
      return "Для rolling-анализа задайте пользовательские веса портфеля.";
    if (
      observations !== undefined &&
      observations < (request.rolling_window_months ?? 120)
    )
      return `Для rolling-окна нужно ${request.rolling_window_months ?? 120} месяцев; доступно ${observations}.`;
    return null;
  }
  const covariance = request.covariance_method === "cv_linear";
  if (observations === undefined || (!portfolio && !covariance)) return null;
  const required = minimumCVHistory(k, !portfolio);
  if (observations >= required) return null;
  return (
    `K=${k}: доступно ${observations} мес., требуется минимум ${required}. ` +
    (portfolio
      ? "Portfolio CV: train ≥120, validation ≥60 мес."
      : "Covariance CV: train ≥60, validation ≥30 мес.")
  );
}
