import type { ApiRequest, Kind } from "../types";

export const CV_FOLDS = Array.from({ length: 10 }, (_, i) => 2 + 2 * i);
export function minimumCVHistory(k: number, covariance = false): number {
  return k === 2 ? (covariance ? 90 : 180) : k * (covariance ? 30 : 60);
}
export function minimumBootstrapHistory(
  k: number,
  covariance: boolean,
): number {
  const outerMinimum = Math.max(240, k * 60);
  if (!covariance) return outerMinimum;
  const innerMinimum = minimumCVHistory(k, true);
  // For even K the shortest one-sided Train is at one of the central folds.
  let months = outerMinimum;
  while (true) {
    const cutoff =
      Math.floor(k / 2) * Math.floor(months / k) + Math.min(k / 2, months % k);
    if (Math.min(cutoff, months - cutoff) >= innerMinimum) return months;
    months++;
  }
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
  if (kind === "bootstrap" && observations !== undefined) {
    const required = minimumBootstrapHistory(k, covariance);
    return observations >= required
      ? null
      : `Bootstrap K=${k}: доступно ${observations} мес., требуется минимум ${required} для полной Validation и допустимого Train.`;
  }
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
