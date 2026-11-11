export type Method = "sample" | "ledoit_wolf" | "nonlinear" | "cv_linear";
export type Kind =
  | "frontier"
  | "cross-validation"
  | "bootstrap"
  | "resampled-frontier"
  | "rolling-efficiency";
export type Mode = "demo" | "live";
export type Bounds = { min: number; max: number };
export interface ApiRequest {
  instrument_ids: number[];
  currency: "USD";
  start_date: string | null;
  end_date: string | null;
  covariance_method: Method;
  shrinkage_target: "identity" | "diagonal";
  return_basis?: "nominal" | "real";
  risk_free_source?: "historical_bil" | "fixed_annual";
  risk_free_rate: number;
  objective: "gmv" | "max_sharpe" | "target_return";
  target_return: number | null;
  asset_constraints: Record<string, Bounds>;
  group_constraints: Record<string, Bounds>;
  asset_groups?: Record<string, number>;
  user_weights?: Record<string, number> | null;
  frontier_points: number;
  cv_folds: number;
  bootstrap_iterations: number;
  expected_block_length: number;
  random_seed: number;
  bootstrap_objectives: ("gmv" | "max_sharpe" | "target_return")[] | null;
  risk_aversions: number[] | null;
  include_efficiency_robustness?: boolean;
  rolling_window_months?: number;
  rolling_step_months?: number;
}
export interface Engine {
  name: string;
  version: string;
  source_sha256: string;
  dependencies: Record<string, string>;
}
export interface SeriesIdentity {
  instrument_id: number;
  series_id: number;
  series_version: string;
  series_sha256: string;
  last_updated_at: string;
  source: string;
  currency: string;
  observation_count: number;
}
export interface Provenance {
  engine: Engine;
  returns_matrix_sha256: string;
  reference_panel_sha256?: string;
  matrix_encoding: string;
  source_series: SeriesIdentity[];
  api_request?: ApiRequest;
  random_seed?: number;
  optimization_parameters?: unknown;
}
export interface Instrument {
  id: number;
  ticker: string;
  name: string | null;
  category: string | null;
  groups: string[];
  start_date: string;
  end_date: string;
  observation_count: number;
  series_id: number;
}
export interface Catalog {
  engine: Engine;
  instruments: Instrument[];
}
export interface Metrics {
  expected_return: number;
  volatility: number;
  historical_volatility?: number;
  sharpe_ratio: number | null;
  historical_sharpe_ratio?: number | null;
  risk_free_mean_return?: number;
  historical_cagr: number;
  historical_max_drawdown: number;
}
export interface Portfolio {
  status: string;
  weights: Record<string, number> | null;
  metrics: Metrics | null;
  diagnostics: Record<string, unknown>;
  target_return?: number;
  risk_aversion?: number;
  feasible?: boolean;
  label?: string;
}
export interface Period {
  start: string;
  end: string;
  observations: number;
}
export interface BaseResult {
  return_basis?: "nominal" | "real";
  risk_free_source?: "historical_bil" | "fixed_annual";
  reference_metadata?: Record<string, unknown>;
  risk_free_mean_return?: number;
  differential_covariance_parameters?: Record<string, unknown>;
  differential_covariance_diagnostics?: Record<string, unknown>;
  asset_portfolios?: Record<string, Portfolio>;
  status: "complete" | "incomplete";
  assets: string[];
  currency: "USD";
  period: Period;
  source_metadata: {
    instrument_id: number;
    ticker: string;
    groups: string[];
    series_id: number;
    start_date: string;
    end_date: string;
  }[];
  covariance_method: Method;
  covariance_parameters: Record<string, unknown>;
  covariance_diagnostics: Record<string, unknown>;
  warnings: string[];
  execution_seconds: number;
  reproducibility: Provenance;
}
export interface FrontierResult extends BaseResult {
  user_portfolio?: Portfolio | null;
  frontier: Portfolio[];
  gmv: Portfolio;
  max_sharpe: Portfolio;
  equal_weight: Portfolio;
  selected_portfolio: Portfolio;
  maximum_return: Portfolio;
}
export interface Fold {
  fold: number;
  training_direction: "train_before_validation" | "train_after_validation";
  status: string;
  weights: Record<string, number> | null;
  training: Period & { segments: Period[] };
  validation: Period;
  training_metrics?: Metrics | null;
  validation_metrics?: {
    realized_annual_arithmetic_return: number;
    realized_annual_volatility: number;
    realized_sharpe_ratio: number | null;
    realized_total_return: number;
  } | null;
  diagnostics: Record<string, unknown>;
}
export interface CVResult extends BaseResult {
  cv_folds: number;
  methodology: "bidirectional_one_sided_block_kfold";
  total_observations: number;
  validation_observations: number;
  validation_coverage_fraction: number;
  validation_interpretation: string;
  original: Portfolio;
  ensemble: Portfolio | null;
  stability: {
    standard_deviation: Record<string, number>;
    minimum: Record<string, number>;
    maximum: Record<string, number>;
    mean_absolute_weight_deviation: number;
    maximum_weight_deviation: number;
    mean_allocation_turnover_from_original: number;
    lower_bound_frequency: Record<string, number>;
    upper_bound_frequency: Record<string, number>;
  } | null;
  efficiency_robustness?: EfficiencyDistribution | null;
  folds: (Fold & { efficiency_robustness?: EfficiencyDistance })[];
}
export interface EfficiencyDistance {
  status: string;
  user_weights?: Record<string, number>;
  user_expected_return?: number | null;
  user_volatility?: number | null;
  return_shortfall: number | null;
  excess_volatility: number | null;
  diagnostics: Record<string, unknown>;
}
export interface EfficiencyDistribution {
  user_weights: Record<string, number>;
  evaluation?: string;
  valid_count: number;
  failed_count: number;
  horizon?: {
    months: number;
    years: number;
    initial_wealth: number;
    sample_length_policy: string;
    rebalancing: string;
  };
  distributions?: Record<string, BootstrapMetricDistribution>;
  return_shortfall: {
    mean: number;
    median: number;
    p5: number;
    p95: number;
    minimum?: number;
    maximum: number;
  } | null;
  excess_volatility: {
    mean: number;
    median: number;
    p5: number;
    p95: number;
    minimum?: number;
    maximum: number;
  } | null;
  results?: (EfficiencyDistance & {
    window_number?: number;
    start_month?: string;
    end_month?: string;
    fold?: number;
    iteration?: number;
    observations?: number;
    portfolio_metrics?: Record<string, number | null>;
  })[];
}
export interface BootstrapMetricDistribution {
  mean: number | null;
  median: number | null;
  p5: number | null;
  p95: number | null;
  minimum: number | null;
  maximum: number | null;
  valid_count: number;
  unavailable_count: number;
  horizon_months: number;
  horizon_years: number;
  annualized: boolean;
  units: string;
}
export interface WeightStats {
  original: number | null;
  mean: number;
  median: number;
  standard_deviation: number;
  p5: number;
  p95: number;
  minimum: number;
  maximum: number;
  lower_bound_frequency: number;
  upper_bound_frequency: number;
}
export interface BootstrapObjective {
  status: string;
  computational_status?: string | null;
  target_return?: number | null;
  target_feasibility_rate?: number | null;
  target_feasible_iterations?: number | null;
  target_infeasible_iterations?: number | null;
  numerical_failure_iterations?: number | null;
  feasibility_unknown_iterations?: number | null;
  original_target_feasible?: boolean | null;
  statistics_condition?: string | null;
  averaging_policy?: string | null;
  resampled_target_evaluation?: {
    expected_return: number;
    target_return: number;
    target_shortfall: number;
    target_surplus: number;
    meets_target: boolean;
    evaluation: string;
    target_guaranteed: boolean;
  } | null;
  requested_iterations: number;
  successful_iterations: number;
  failed_iterations: number;
  original: Portfolio;
  resampled: Portfolio | null;
  stability: {
    assets: Record<string, WeightStats>;
    allocation_distance: {
      mean: number;
      median: number;
      p5: number;
      p95: number;
    } | null;
  } | null;
  iterations: {
    iteration: number;
    status: string;
    weights: Record<string, number> | null;
    metrics?: Metrics;
    diagnostics?: Record<string, unknown>;
    reason?: string;
  }[];
  failure_reasons: Record<string, number>;
  failures: unknown[];
}
export interface BootstrapResult extends BaseResult {
  conditional_bootstrap: boolean;
  sample_fingerprint: string;
  objectives: Record<string, BootstrapObjective>;
  efficiency_robustness?: EfficiencyDistribution | null;
}
export interface ResampledResult extends BaseResult {
  frontier: Portfolio[];
  user_portfolio?: Portfolio | null;
  user_sample_average?: {
    expected_return: number;
    volatility: number;
    successful_samples: number;
  } | null;
  risk_aversions: number[];
  requested_iterations: number;
  successful_iterations: number;
  failed_iterations: number;
  failure_reasons: Record<string, number>;
  conditional_bootstrap: boolean;
}
export type Result =
  | FrontierResult
  | CVResult
  | BootstrapResult
  | ResampledResult
  | RollingEfficiencyResult;
export interface RollingEfficiencyResult {
  status: "complete" | "incomplete";
  methodology: string;
  window_months: number;
  step_months: number;
  covariance_method: Method;
  requested_cv_folds: number;
  effective_cv_folds: number | null;
  user_weights: Record<string, number>;
  return_basis: "nominal" | "real";
  risk_free_source: "historical_bil" | "fixed_annual";
  period: Period;
  assets: string[];
  currency: "USD";
  source_metadata: BaseResult["source_metadata"];
  reference_metadata?: Record<string, unknown>;
  covariance_parameters?: Record<string, unknown>;
  covariance_diagnostics?: Record<string, unknown>;
  differential_covariance_parameters?: Record<string, unknown>;
  differential_covariance_diagnostics?: Record<string, unknown>;
  warnings: string[];
  execution_seconds: number;
  windows: (EfficiencyDistance & {
    window_number: number;
    start_month: string;
    end_month: string;
    observations: number;
    effective_cv_folds: number | null;
    covariance_parameters: Record<string, unknown>;
    covariance_diagnostics: Record<string, unknown>;
  })[];
  summary: Omit<
    EfficiencyDistribution,
    "user_weights" | "results" | "evaluation"
  >;
  reproducibility: Provenance;
}
export interface Calculation {
  id: string;
  kind: Kind;
  request: ApiRequest;
  result: Result;
  origin: Mode;
  created_at: string;
}
export interface Job {
  job_id: string;
  kind: Kind;
  status: "running" | "completed" | "failed" | "timed_out";
  result_url: string;
  status_url: string;
  timeout_seconds: number;
  elapsed_seconds: number;
  error: string | null;
  result?: Result | null;
}
export interface Snapshot {
  schema: "qcross-portfolio-lab/v1";
  created_at: string;
  instruments: Instrument[];
  calculations: Calculation[];
}
export interface DemoBundle {
  catalog: Catalog;
  calculations: Calculation[];
}
export const METHODS: {
  value: Method;
  name: string;
  short: string;
  color: string;
}[] = [
  {
    value: "sample",
    name: "Sample Covariance",
    short: "Sample",
    color: "#256478",
  },
  {
    value: "ledoit_wolf",
    name: "Ledoit–Wolf Linear Shrinkage",
    short: "Ledoit–Wolf",
    color: "#b98943",
  },
  {
    value: "nonlinear",
    name: "Analytical Nonlinear Shrinkage",
    short: "Nonlinear",
    color: "#7b7099",
  },
  {
    value: "cv_linear",
    name: "Cross-Validated Linear Shrinkage",
    short: "CV Linear",
    color: "#579b7d",
  },
];
export const DEFAULT_REQUEST: ApiRequest = {
  return_basis: "nominal",
  risk_free_source: "historical_bil",
  instrument_ids: [1, 175, 243],
  currency: "USD",
  start_date: "1990-01-01",
  end_date: null,
  covariance_method: "nonlinear",
  shrinkage_target: "identity",
  risk_free_rate: 0.03,
  objective: "gmv",
  target_return: null,
  asset_constraints: {},
  group_constraints: {},
  frontier_points: 51,
  cv_folds: 4,
  bootstrap_iterations: 1000,
  expected_block_length: 12,
  random_seed: 42,
  bootstrap_objectives: ["gmv", "max_sharpe"],
  risk_aversions: null,
  include_efficiency_robustness: true,
  rolling_window_months: 120,
  rolling_step_months: 12,
};
