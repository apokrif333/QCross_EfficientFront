import { z } from "zod";
import type { Kind } from "../types";
const n = z.number().finite(),
  hash = z.string().regex(/^[a-f0-9]{64}$/),
  weights = z.record(z.string(), n);
const bounds = z
  .object({ min: n.min(0).max(1), max: n.min(0).max(1) })
  .refine((b) => b.min <= b.max);
const requestFields = z.object({
  portfolio_controls: z
    .object({
      mode: z.enum(["none", "downside_vol", "trend"]),
      hedge_weights: weights.refine((w) =>
        Object.values(w).every((v) => v >= 0 && v <= 1),
      ),
      trend: z
        .object({
          destination: z.enum(["remaining", "hedge"]),
          asset_rules: z.record(
            z.string(),
            z.object({
              indicator: z.enum(["disabled", "sma", "momentum"]),
              lookback_months: n.int().min(2).max(36),
            }),
          ),
          sensitivity_asset: z.string().nullish(),
        })
        .nullish(),
      downside_vol: z
        .object({
          target_annual: n.positive().max(10),
          lookback_months: n.int().min(3).max(24),
        })
        .nullish(),
    })
    .refine(
      (c) =>
        c.mode === "none" ||
        (Math.abs(
          Object.values(c.hedge_weights).reduce((a, b) => a + b, 0) - 1,
        ) < 1e-8 &&
          (c.mode === "downside_vol" ? !!c.downside_vol : !!c.trend)),
      "Укажите хедж 100% и параметры управления",
    )
    .optional(),
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
  transaction_fee_rate: n.min(0).lt(1).optional(),
  rebalancing_interval_months: n.int().min(1).max(1200).nullable().optional(),
  objective: z.enum([
    "gmv",
    "max_sharpe",
    "target_return",
    "max_return_at_user_risk",
  ]),
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
    .array(
      z.enum(["gmv", "max_sharpe", "target_return", "max_return_at_user_risk"]),
    )
    .min(1)
    .max(4)
    .refine((items) => new Set(items).size === items.length)
    .nullable(),
  risk_aversions: z.array(n.positive()).nullable(),
  include_efficiency_robustness: z.boolean().optional(),
  include_paired_comparison: z.boolean().optional(),
  rolling_window_months: n.int().min(120).max(10000).optional(),
  rolling_step_months: n.int().min(1).max(120).optional(),
});
export const requestSchema = requestFields
  .refine(
    (r) =>
      (r.objective !== "max_return_at_user_risk" &&
        !r.bootstrap_objectives?.includes("max_return_at_user_risk")) ||
      Math.abs(
        Object.values(r.user_weights ?? {}).reduce((a, b) => a + b, 0) - 1,
      ) < 1e-8,
    {
      message: "Укажите веса вашего портфеля, сумма 100%",
      path: ["user_weights"],
    },
  )
  .refine(
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
export const rollingRequestSchema = requestFields.refine(
  (r) => {
    const entries = Object.entries(r.user_weights ?? {});
    return (
      entries.length > 0 &&
      entries.every(([asset]) =>
        r.instrument_ids.map(String).includes(asset),
      ) &&
      Math.abs(entries.reduce((total, [, weight]) => total + weight, 0) - 1) <=
        1e-8
    );
  },
  {
    message: "Rolling efficiency requires explicit user weights totaling 100%.",
    path: ["user_weights"],
  },
);
export function requestSchemaFor(kind: Kind) {
  if (kind === "rebalancing-optimization")
    return requestFields.refine(
      (r) =>
        Math.abs(
          Object.values(r.user_weights ?? {}).reduce((a, b) => a + b, 0) - 1,
        ) < 1e-8,
      {
        message: "Explicit user weights totaling 100% are required",
        path: ["user_weights"],
      },
    );
  if (kind === "resampled-frontier") return resampledRequestSchema;
  if (kind === "rolling-efficiency") return rollingRequestSchema;
  if (kind === "bootstrap")
    return requestSchema.refine(
      (r) =>
        !r.user_weights ||
        r.include_paired_comparison === false ||
        r.bootstrap_iterations % 2 === 0,
      {
        message: "Для парного Bootstrap нужно чётное число генераций",
        path: ["bootstrap_iterations"],
      },
    );
  return requestSchema;
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
const efficiencyDistance = z
  .object({
    status: z.string(),
    return_shortfall: n.nonnegative().nullable().optional(),
    excess_volatility: n.nonnegative().nullable().optional(),
    user_expected_return: n.nullable().optional(),
    user_volatility: n.nonnegative().nullable().optional(),
    diagnostics: z.record(z.string(), z.unknown()),
    portfolio_metrics: z
      .record(
        z.string(),
        z.union([n.nullable(), z.record(z.string(), n.nullable())]),
      )
      .optional(),
  })
  .passthrough()
  .refine(
    (r) =>
      r.status !== "optimal" ||
      (r.return_shortfall != null && r.excess_volatility != null),
  );
const distanceStats = z.object({
  mean: n.nonnegative(),
  median: n.nonnegative(),
  p5: n.nonnegative(),
  p95: n.nonnegative(),
  minimum: n.nonnegative().optional(),
  maximum: n.nonnegative(),
});
const distanceSummary = z.object({
  valid_count: n.int().nonnegative(),
  failed_count: n.int().nonnegative(),
  return_shortfall: distanceStats.nullable(),
  excess_volatility: distanceStats.nullable(),
});
const bootstrapMetricDistribution = z.object({
  mean: n.nullable(),
  median: n.nullable(),
  p5: n.nullable(),
  p95: n.nullable(),
  minimum: n.nullable(),
  maximum: n.nullable(),
  valid_count: n.int().nonnegative(),
  unavailable_count: n.int().nonnegative(),
  horizon_months: n.int().positive(),
  horizon_years: n.positive(),
  annualized: z.boolean(),
  units: z.string(),
});
const efficiencyDistribution = distanceSummary
  .extend({
    user_weights: weights,
    results: z.array(efficiencyDistance).optional(),
    horizon: z
      .object({
        months: n.int().positive(),
        years: n.positive(),
        initial_wealth: n.positive(),
        sample_length_policy: z.string(),
        rebalancing: z.string(),
      })
      .optional(),
    distributions: z.record(z.string(), bootstrapMetricDistribution).optional(),
  })
  .passthrough();
const pairedComparison = z
  .object({
    generated_datasets: n.int().nonnegative().optional(),
    horizon_months: n.int().positive().optional(),
    clouds: z
      .array(
        z.object({
          pair: n.int().positive(),
          assets: z.record(
            z.string(),
            z.object({ expected_return: n, volatility: n.nonnegative() }),
          ),
          user: z.object({ expected_return: n, volatility: n.nonnegative() }),
          optimized: z
            .object({ expected_return: n, volatility: n.nonnegative() })
            .nullable(),
        }),
      )
      .optional(),
    validation_frontier: z
      .array(z.object({ expected_return: n, volatility: n.nonnegative() }))
      .optional(),
    valid_count: n.int().nonnegative(),
    failed_count: n.int().nonnegative(),
    requested_comparisons: n.int().nonnegative(),
    differences: z.record(
      z.string(),
      z
        .object({
          mean: n,
          median: n,
          p5: n,
          p95: n,
          minimum: n,
          maximum: n,
          valid_count: n.int().nonnegative(),
        })
        .nullable(),
    ),
    win_frequencies: z.record(
      z.string(),
      z.object({
        fraction: n.min(0).max(1).nullable(),
        denominator: n.int().nonnegative(),
      }),
    ),
    results: z.array(z.object({ status: z.string() }).passthrough()),
  })
  .passthrough();
const base = z.object({
  paired_comparison: pairedComparison.nullable().optional(),
  efficiency_robustness: efficiencyDistribution.nullable().optional(),
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
const simulationStats = z.object({
  annualized_cagr: n.nullable(),
  annualized_arithmetic_return: n,
  annualized_volatility: n.nonnegative(),
  historical_sharpe_ratio: n.nullable(),
  sortino_ratio: n.nullable(),
  maximum_drawdown: n.min(0).max(1),
  terminal_wealth: n.nonnegative().nullable(),
});
const results = {
  "rebalancing-optimization": base
    .extend({
      methodology: z.literal("historical_in_sample_rebalancing_comparison_v1"),
      user_weights: weights,
      months: z.array(z.string()),
      transaction_fee_rate: n.min(0).lt(1),
      strategies: z
        .array(
          z
            .object({
              id: z.string(),
              label: z.string(),
              calendar_months: n.int().positive().nullable(),
              threshold_percent: n.positive().nullable(),
              annual_baseline: z.boolean(),
              net: simulationStats,
              gross: simulationStats,
              net_wealth: z.array(n.nonnegative().nullable()),
              gross_wealth: z.array(n.nonnegative().nullable()),
              weight_history: z.array(z.array(n)),
              events: z.array(
                z
                  .object({
                    date: z.string(),
                    kind: z.string(),
                    triggering_assets: z.array(z.string()),
                    trades: weights,
                    commissions: n.nonnegative(),
                    turnover: n.nonnegative(),
                  })
                  .passthrough(),
              ),
            })
            .passthrough(),
        )
        .min(20)
        .max(21),
    })
    .passthrough(),
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
      user_portfolio: portfolio.nullable().optional(),
      user_sample_average: z
        .object({
          expected_return: n,
          volatility: n.nonnegative(),
          successful_samples: n.int().positive(),
        })
        .nullable()
        .optional(),
      risk_aversions: z.array(n),
      conditional_bootstrap: z.boolean(),
    })
    .passthrough(),
  "rolling-efficiency": z
    .object({
      status: z.enum(["complete", "incomplete"]),
      methodology: z.string(),
      windows: z.array(
        z
          .object({
            window_number: n.int(),
            start_month: z.string(),
            end_month: z.string(),
            observations: n.int(),
          })
          .passthrough()
          .and(efficiencyDistance),
      ),
      summary: distanceSummary,
      user_weights: weights,
      period: period,
      assets: z.array(z.string()),
      currency: z.literal("USD"),
      source_metadata: z.array(z.unknown()),
      warnings: z.array(z.string()),
      execution_seconds: n,
      reproducibility: provenance,
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
        "rolling-efficiency",
        "rebalancing-optimization",
      ]),
      request: requestFields,
      origin: z.enum(["demo", "live"]),
      created_at: z.string(),
      result: z.unknown(),
    })
    .parse(value);
  requestSchemaFor(record.kind).parse({
    ...record.request,
    include_paired_comparison:
      record.request.include_paired_comparison ?? false,
  });
  const r = results[record.kind].parse(record.result);
  requestSchemaFor(record.kind).parse({
    ...r.reproducibility.api_request,
    include_paired_comparison:
      r.reproducibility.api_request.include_paired_comparison ?? false,
  });
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
