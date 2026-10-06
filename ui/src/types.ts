export type Method = "sample" | "ledoit_wolf" | "nonlinear" | "cv_linear";
export type Kind =
  "frontier" | "cross-validation" | "bootstrap" | "resampled-frontier";
export type Mode = "demo" | "live";
export type Bounds = { min: number; max: number };
export interface ApiRequest {
  instrument_ids: number[];
  currency: "USD";
  start_date: string | null;
  end_date: string | null;
  covariance_method: Method;
  shrinkage_target: "identity" | "diagonal";
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
  bootstrap_objectives: ("gmv" | "max_sharpe")[] | null;
  risk_aversions: number[] | null;
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
  status: string;
  weights: Record<string, number> | null;
  training: Period & { segments: Period[] };
  validation: Period;
  training_metrics?: Metrics;
  validation_metrics?: {
    realized_annual_arithmetic_return: number;
    realized_annual_volatility: number;
    realized_sharpe_ratio: number | null;
    realized_total_return: number;
  };
  diagnostics: Record<string, unknown>;
}
export interface CVResult extends BaseResult {
  original: Portfolio;
  folds: Fold[];
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
}
export interface WeightStats {
  original: number;
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
    };
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
}
export interface ResampledResult extends BaseResult {
  frontier: Portfolio[];
  risk_aversions: number[];
  requested_iterations: number;
  successful_iterations: number;
  failed_iterations: number;
  failure_reasons: Record<string, number>;
  conditional_bootstrap: boolean;
}
export type Result =
  FrontierResult | CVResult | BootstrapResult | ResampledResult;
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
  instrument_ids: [1, 175, 243],
  currency: "USD",
  start_date: "1990-01-01",
  end_date: null,
  covariance_method: "sample",
  shrinkage_target: "identity",
  risk_free_rate: 0.03,
  objective: "gmv",
  target_return: null,
  asset_constraints: {},
  group_constraints: {},
  frontier_points: 51,
  cv_folds: 5,
  bootstrap_iterations: 1000,
  expected_block_length: 12,
  random_seed: 42,
  bootstrap_objectives: ["gmv", "max_sharpe"],
  risk_aversions: null,
};
