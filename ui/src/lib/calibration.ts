export const CALIBRATION_HORIZONS = [3, 6, 12, 24, 60, 120, 240];

export const CALIBRATION_DEFAULTS = {
  start_date: null as string | null,
  end_date: null as string | null,
  train_length: 240,
  test_length: 240,
  step: null as number | null,
  generations: 1000,
  min_block_length: 3,
  max_block_length: 24,
  block_length_step: 1,
  random_seed: 42,
  reference_block_length: 12,
  practical_tolerance: 0.05,
  final_holdout: true,
  horizons: CALIBRATION_HORIZONS,
};
export type CalibrationSettings = typeof CALIBRATION_DEFAULTS;
export type CalibrationRequest = CalibrationSettings & {
  instrument_ids: number[];
  return_basis: "nominal" | "real";
};

type Period = { start: string; end: string; months: number };
type NamedAsset = { instrument_id: number; ticker: string; source?: string };
export type CalibrationFold = {
  fold: number;
  train: Period;
  test: Period;
  holdout: boolean;
  intervals: Record<string, number>;
};
export type CalibrationPreview = {
  ready: boolean;
  history: Period;
  folds: CalibrationFold[];
  holdout: CalibrationFold | null;
  full_calibration_folds: number;
  lengths: number[];
  horizons: number[];
  interval_counts: Record<string, number>;
  generations: number;
  trajectory_count: number;
  warnings: string[];
  assets: NamedAsset[];
};

export type ExtraCalibrationScore =
  "energy" | "temporal_returns" | "temporal_absolute" | "cross_asset";
export type CalibrationScore = "crps" | ExtraCalibrationScore;
export type Ranking = Record<ExtraCalibrationScore, number> & {
  length: number;
  score: number;
  relative_difference: number | null;
  comparable: boolean;
};
export type Scores = Record<string, number | null>;
export type ScoreMatrix = Record<string, Scores>;
export type CalibrationResult = {
  methodology: string;
  phase: "calibration" | "holdout";
  holdout_status:
    "available" | "unavailable" | "evaluated" | "previously_viewed";
  preview: CalibrationPreview;
  optimal_length: number;
  minimum_score: number;
  ranking: Ranking[];
  rows: (Record<CalibrationScore, number> & {
    fold: number;
    length: number;
    horizon: number;
    intervals: number;
    group_weights: Scores;
    primary_metrics: Scores;
    available_members: number;
    minimum_scale_count: number;
  })[];
  group_weights: Scores;
  horizon_weights: Scores;
  by_horizon: ScoreMatrix;
  by_fold: ScoreMatrix;
  by_group: ScoreMatrix;
  by_metric: ScoreMatrix;
  best_by_horizon: Scores;
  best_by_group: Scores;
  sensitivity: Partial<Record<"fold" | "horizon", Scores>>;
  pit: Record<
    string,
    {
      count: number;
      histogram: number[];
      p05_p95_coverage: number | null;
      p25_p75_coverage: number | null;
      median_absolute_deviation: number | null;
    }
  >;
  metrics: Record<
    string,
    { group: string; minimum_scale: number; primary: boolean }
  >;
  runs: Record<string, Record<string, Record<string, string[][]>>>;
  reproducibility: Record<string, unknown>;
  execution_seconds: number;
  demo_detail_lengths?: number[];
};

export type ForecastDistribution = {
  min: number;
  p05: number;
  p25: number;
  median: number;
  p75: number;
  p95: number;
  max: number;
  mean: number;
  std: number;
  undefined: number;
  histogram: number[];
  crps: (number | null)[];
  mean_crps: number | null;
  ranks: (number | null)[];
};
export type CalibrationDetail = {
  intervals: Period[];
  metrics: Record<
    string,
    {
      scale: number;
      scale_method: string;
      reference_count: number;
      train: (number | null)[];
      actual: (number | null)[];
      series: Record<string, ForecastDistribution>;
    }
  >;
};
export type CalibrationDemo = {
  calibration: CalibrationResult;
  holdout: CalibrationResult;
  calibration_detail: CalibrationDetail;
  holdout_detail: CalibrationDetail;
};

export const GROUP_LABELS: Record<string, string> = {
  returns: "Return Distribution",
  risk: "Risk Distribution",
  temporal: "Temporal Dependence",
  drawdowns: "Market Cycles and Drawdowns",
  cross_asset: "Cross-Asset Dependence",
};
export const SCORE_LABELS: Record<CalibrationScore, string> = {
  crps: "Aggregated Normalized CRPS",
  energy: "Energy Score",
  temporal_returns: "Variogram Score · Returns",
  temporal_absolute: "Variogram Score · Absolute Returns",
  cross_asset: "Variogram Score · Cross-Asset",
};

export function metricLabel(metric: string) {
  return metric.startsWith("acf_")
    ? "ACF " + metric.slice(4).replaceAll("_", " · lag ")
    : metric.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase());
}

export const horizonLabel = (months: number | string) => months + " мес.";

export const scoreNumber = (value: number | null | undefined) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("ru-RU", { maximumSignificantDigits: 5 }).format(
        value,
      );
export const scorePercent = (value: number | null | undefined) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("ru-RU", {
        style: "percent",
        maximumFractionDigits: 1,
      }).format(value);

export function bestLength(scores: Scores) {
  return Object.entries(scores)
    .filter(([, score]) => score !== null)
    .sort(([l1, s1], [l2, s2]) => s1! - s2! || Number(l1) - Number(l2))[0]?.[0];
}
