import { z } from "zod";
import type { Kind } from "../types";
const n = z.number().finite(),
  hash = z.string().regex(/^[a-f0-9]{64}$/),
  weights = z.record(z.string(), n);
const bounds = z
  .object({ min: n.min(0).max(1), max: n.min(0).max(1) })
  .refine((b) => b.min <= b.max);
const requestFields = z.object({
  instrument_ids: z
    .array(z.number().int().positive())
    .min(2)
    .max(15)
    .refine((ids) => new Set(ids).size === ids.length),
  currency: z.literal("USD"),
  start_date: z.string().nullable(),
  end_date: z.string().nullable(),
  covariance_method: z.enum([
    "sample",
    "ledoit_wolf",
    "nonlinear",
    "cv_linear",
  ]),
  shrinkage_target: z.enum(["identity", "diagonal"]),
  return_basis: z.enum(["nominal", "real"]).optional(),
  risk_free_source: z.enum(["historical_bil", "fixed_annual"]).optional(),
  risk_free_rate: n.gt(-1).max(1),
  objective: z.enum(["gmv", "max_sharpe", "target_return"]),
  target_return: n.min(-12).max(12).nullable(),
  asset_constraints: z.record(z.string(), bounds),
  group_constraints: z.record(z.string(), bounds),
  asset_groups: z.record(z.string(), n.int().min(1).max(5)).optional(),
  user_weights: z.record(z.string(), n.min(0).max(1)).nullable().optional(),
  frontier_points: n.int().min(2).max(201),
  cv_folds: n.int().min(2).max(20).multipleOf(2),
  bootstrap_iterations: n.int().min(1).max(2000),
  expected_block_length: n.min(3).max(24),
  random_seed: n.int().min(0).max(4294967295),
  bootstrap_objectives: z
    .array(z.enum(["gmv", "max_sharpe", "target_return"]))
    .min(1)
    .max(3)
    .refine((items) => new Set(items).size === items.length)
    .nullable(),
  risk_aversions: z.array(n.positive()).nullable(),
});
export const requestSchema = requestFields.refine(
  (r) =>
    (r.objective !== "target_return" &&
      !r.bootstrap_objectives?.includes("target_return")) ||
    r.target_return !== null,
  {
    message: "Укажите минимальную целевую годовую доходность",
    path: ["target_return"],
  },
);
export const resampledRequestSchema = requestFields.extend({
  bootstrap_iterations: n.int().min(1).max(500),
});
export function requestSchemaFor(kind: Kind) {
  return kind === "resampled-frontier" ? resampledRequestSchema : requestSchema;
}
const metrics = z.object({
  expected_return: n,
  volatility: n,
  historical_volatility: n.optional(),
  sharpe_ratio: n.nullable(),
  historical_sharpe_ratio: n.nullable().optional(),
  risk_free_mean_return: n.optional(),
  historical_cagr: n,
  historical_max_drawdown: n,
});
const portfolio = z
  .object({
    status: z.string(),
    weights: weights.nullable(),
    metrics: metrics.nullable(),
    diagnostics: z.record(z.string(), z.unknown()),
  })
  .passthrough();
const period = z
  .object({
    start: z.string(),
    end: z.string(),
    observations: n.int().positive(),
  })
  .passthrough();
const provenance = z
  .object({
    engine: z.object({
      name: z.string(),
      version: z.string(),
      source_sha256: hash,
      dependencies: z.record(z.string(), z.string()),
    }),
    returns_matrix_sha256: hash,
    matrix_encoding: z.string(),
    source_series: z
      .array(
        z.object({
          instrument_id: n.int(),
          series_id: n.int(),
          series_version: hash,
          series_sha256: hash,
          last_updated_at: z.string(),
          source: z.string(),
          currency: z.literal("USD"),
          observation_count: n.int(),
        }),
      )
      .min(2),
    api_request: requestFields,
    random_seed: n.int(),
    optimization_parameters: z.record(z.string(), z.unknown()),
  })
  .passthrough();
const base = z.object({
  asset_portfolios: z.record(z.string(), portfolio).optional(),
  status: z.enum(["complete", "incomplete"]),
  assets: z.array(z.string()).min(2).max(15),
  currency: z.literal("USD"),
  period,
  source_metadata: z.array(
    z
      .object({
        instrument_id: n.int(),
        ticker: z.string(),
        groups: z.array(z.string()),
        series_id: n.int(),
        start_date: z.string(),
        end_date: z.string(),
      })
      .passthrough(),
  ),
  covariance_method: requestSchema.shape.covariance_method,
  covariance_parameters: z.record(z.string(), z.unknown()),
  covariance_diagnostics: z.record(z.string(), z.unknown()),
  warnings: z.array(z.string()),
  execution_seconds: n,
  reproducibility: provenance,
});
const stability = z
  .object({
    standard_deviation: weights,
    minimum: weights,
    maximum: weights,
    mean_absolute_weight_deviation: n,
    maximum_weight_deviation: n,
    mean_allocation_turnover_from_original: n,
    lower_bound_frequency: weights,
    upper_bound_frequency: weights,
  })
  .passthrough();
const assetStats = z.object({
  original: n.nullable(),
  mean: n,
  median: n,
  standard_deviation: n,
  p5: n,
  p95: n,
  minimum: n,
  maximum: n,
  lower_bound_frequency: n,
  upper_bound_frequency: n,
});
const failures = {
  requested_iterations: n.int(),
  successful_iterations: n.int(),
  failed_iterations: n.int(),
  failure_reasons: z.record(z.string(), n),
};
const iteration = z
  .object({
    iteration: n.int(),
    status: z.string(),
    weights: weights.nullable(),
  })
  .passthrough();
const results = {
  frontier: base
    .extend({
      frontier: z.array(portfolio),
      gmv: portfolio,
      max_sharpe: portfolio,
      equal_weight: portfolio,
      user_portfolio: portfolio.nullable().optional(),
      selected_portfolio: portfolio,
      maximum_return: portfolio,
    })
    .passthrough(),
  "cross-validation": base
    .extend({
      cv_folds: n.int().min(2).max(20).multipleOf(2),
      methodology: z.literal("bidirectional_one_sided_block_kfold"),
      total_observations: n.int(),
      validation_observations: n.int(),
      validation_coverage_fraction: n.min(0).max(1),
      validation_interpretation: z.string(),
      original: portfolio,
      ensemble: portfolio.nullable(),
      stability: stability.nullable(),
      folds: z.array(
        z
          .object({
            fold: n.int(),
            training_direction: z.enum([
              "train_before_validation",
              "train_after_validation",
            ]),
            status: z.string(),
            weights: weights.nullable(),
            training: period.extend({ segments: z.array(period) }),
            validation: period,
            training_metrics: metrics.nullable().optional(),
            validation_metrics: z
              .object({
                realized_annual_arithmetic_return: n,
                realized_annual_volatility: n,
                realized_sharpe_ratio: n.nullable(),
                realized_total_return: n,
              })
              .nullable()
              .optional(),
            diagnostics: z.record(z.string(), z.unknown()),
          })
          .passthrough(),
      ),
    })
    .passthrough(),
  bootstrap: base
    .extend({
      conditional_bootstrap: z.boolean(),
      sample_fingerprint: z.string(),
      objectives: z
        .record(
          z.string(),
          z
            .object({
              ...failures,
              status: z.string(),
              computational_status: z.string().nullable().optional(),
              target_return: n.nullable().optional(),
              target_feasibility_rate: n.min(0).max(1).nullable().optional(),
              target_feasible_iterations: n
                .int()
                .nonnegative()
                .nullable()
                .optional(),
              target_infeasible_iterations: n
                .int()
                .nonnegative()
                .nullable()
                .optional(),
              numerical_failure_iterations: n
                .int()
                .nonnegative()
                .nullable()
                .optional(),
              feasibility_unknown_iterations: n
                .int()
                .nonnegative()
                .nullable()
                .optional(),
              original_target_feasible: z.boolean().nullable().optional(),
              resampled_target_evaluation: z
                .object({
                  expected_return: n,
                  target_return: n,
                  target_shortfall: n.nonnegative(),
                  target_surplus: n.nonnegative(),
                  meets_target: z.boolean(),
                  evaluation: z.string(),
                  target_guaranteed: z.boolean(),
                })
                .nullable()
                .optional(),
              original: portfolio,
              resampled: portfolio.nullable(),
              stability: z
                .object({
                  assets: z.record(z.string(), assetStats),
                  allocation_distance: z
                    .object({
                      mean: n,
                      median: n,
                      p5: n,
                      p95: n,
                    })
                    .nullable(),
                })
                .nullable(),
              iterations: z.array(iteration),
              failures: z.array(z.unknown()),
            })
            .passthrough(),
        )
        .refine((o) => Object.keys(o).length > 0),
    })
    .passthrough(),
  "resampled-frontier": base
    .extend({
      ...failures,
      frontier: z.array(portfolio),
      risk_aversions: z.array(n),
      conditional_bootstrap: z.boolean(),
    })
    .passthrough(),
};
export function validateRecord(value: unknown): void {
  const record = z
    .object({
      id: z.string(),
      kind: z.enum([
        "frontier",
        "cross-validation",
        "bootstrap",
        "resampled-frontier",
      ]),
      request: requestFields,
      origin: z.enum(["demo", "live"]),
      created_at: z.string(),
      result: z.unknown(),
    })
    .parse(value);
  requestSchemaFor(record.kind).parse(record.request);
  const r = results[record.kind].parse(record.result);
  requestSchemaFor(record.kind).parse(r.reproducibility.api_request);
  if (
    JSON.stringify(record.request) !==
    JSON.stringify(r.reproducibility.api_request)
  ) {
    // Object property order may differ after external JSON serialization.
    const normalized = (x: unknown): string =>
      JSON.stringify(x, (_k, v) =>
        v && typeof v === "object" && !Array.isArray(v)
          ? Object.fromEntries(Object.entries(v).sort())
          : v,
      );
    if (
      normalized(record.request) !== normalized(r.reproducibility.api_request)
    )
      throw new Error("Запрос снимка отличается от запроса в provenance.");
  }
  if (
    r.assets.join(",") !== record.request.instrument_ids.map(String).join(",")
  )
    throw new Error("Порядок активов не совпадает с запросом.");
  const checkWeights = (item: unknown): void => {
    if (!item || typeof item !== "object") return;
    for (const [key, value] of Object.entries(item)) {
      if (
        key === "weights" &&
        value !== null &&
        Object.keys(value).sort().join(",") !== [...r.assets].sort().join(",")
      )
        throw new Error("Неполный вектор весов.");
      checkWeights(value);
    }
  };
  checkWeights(r);
  if (record.kind === "bootstrap") {
    const b = results.bootstrap.parse(record.result);
    for (const o of Object.values(b.objectives))
      if (
        o.stability &&
        Object.keys(o.stability.assets).sort().join(",") !==
          [...r.assets].sort().join(",")
      )
        throw new Error("Неполная статистика весов.");
  }
}
export const instrumentSchema = z.object({
  id: n.int().positive(),
  ticker: z.string(),
  name: z.string().nullable(),
  category: z.string().nullable(),
  groups: z.array(z.string()),
  start_date: z.string(),
  end_date: z.string(),
  observation_count: n.int().positive(),
  series_id: n.int().positive(),
});
