"""Rolling sample-sensitivity analysis for one fixed user allocation."""

import hashlib
import json
import time

import numpy as np

from app.analytics.constraints import build_constraints, user_allocation
from app.analytics.efficiency_distance import efficiency_distance, summarize_distances
from app.analytics.estimators import estimate_covariance
from app.analytics.models import AnalyticsError, ReturnPanel
from app.analytics.provenance import panel_provenance
from app.analytics.splitting import MIN_COVARIANCE_TRAIN, MIN_COVARIANCE_VALIDATION, eligible_folds


def analyze_rolling_efficiency(
    panel: ReturnPanel,
    *,
    user_weights: dict,
    window_months: int = 120,
    step_months: int = 12,
    covariance_method: str = "sample",
    cv_folds: int = 4,
    shrinkage_target: str = "identity",
    asset_constraints: dict | None = None,
    group_constraints: dict | None = None,
    risk_free_rate: float = 0.03,
) -> dict:
    if window_months < 120 or step_months < 1:
        raise AnalyticsError(
            "Rolling analysis requires windows of at least 120 months and positive steps."
        )
    if len(panel.values) < window_months:
        raise AnalyticsError(
            f"Rolling window requires {window_months} common months; "
            f"only {len(panel.values)} are available."
        )
    weights = user_allocation(panel.assets, user_weights)
    if weights is None:
        raise AnalyticsError(
            "Rolling efficiency analysis requires explicit user portfolio weights."
        )
    constraints = build_constraints(
        panel.assets, asset_constraints, group_constraints, panel.groups
    )
    indices = range(0, len(panel.values) - window_months + 1, step_months)
    records = []
    effective_fold_counts = []
    started = time.perf_counter()
    for start in indices:
        end = start + window_months
        values = panel.values[start:end]
        folds = None
        if covariance_method == "cv_linear":
            eligible = [
                k
                for k in eligible_folds(
                    len(values),
                    min_train=MIN_COVARIANCE_TRAIN,
                    min_validation=MIN_COVARIANCE_VALIDATION,
                )
                if k <= cv_folds
            ]
            if not eligible:
                raise AnalyticsError(
                    "No eligible covariance CV fold count for "
                    f"{window_months}-month rolling window."
                )
            folds = 2 if len(values) == 120 and 2 in eligible else max(eligible)
            effective_fold_counts.append(folds)
        try:
            estimate = estimate_covariance(
                values, covariance_method, folds=folds or cv_folds, target=shrinkage_target
            )
            result = efficiency_distance(
                values,
                estimate.matrix,
                weights,
                constraints,
                estimator_parameters=estimate.parameters,
                covariance_diagnostics=estimate.diagnostics,
            )
        except (AnalyticsError, np.linalg.LinAlgError) as exc:
            result = {"status": "failed", "diagnostics": {"reason": str(exc)}}
            estimate = None
        records.append(
            {
                "window_number": len(records) + 1,
                "start_month": str(panel.returns.index[start]),
                "end_month": str(panel.returns.index[end - 1]),
                "observations": window_months,
                **result,
                "effective_cv_folds": folds,
                "covariance_parameters": estimate.parameters if estimate else {},
                "covariance_diagnostics": estimate.diagnostics if estimate else {},
            }
        )
    summary = summarize_distances(records)
    provenance = panel_provenance(panel)
    identity = {
        "panel": provenance["reference_panel_sha256"],
        "user_weights": weights.tolist(),
        "window": window_months,
        "step": step_months,
        "method": covariance_method,
        "folds": cv_folds,
        "target": shrinkage_target,
        "asset_constraints": asset_constraints or {},
        "group_constraints": group_constraints or {},
        "groups": panel.groups,
        "engine": provenance["engine"],
    }
    return {
        "status": "complete" if summary["failed_count"] == 0 else "incomplete",
        "methodology": "rolling_fixed_portfolio_efficiency_distance",
        "window_months": window_months,
        "step_months": step_months,
        "covariance_method": covariance_method,
        "requested_cv_folds": cv_folds,
        "effective_cv_folds": max(effective_fold_counts) if effective_fold_counts else None,
        "user_weights": dict(zip(panel.assets, weights.tolist(), strict=True)),
        "return_basis": panel.return_basis,
        "risk_free_source": panel.risk_free_source,
        "period": {
            "start": str(panel.returns.index[0]),
            "end": str(panel.returns.index[-1]),
            "observations": len(panel.values),
        },
        "assets": panel.assets,
        "currency": "USD",
        "source_metadata": panel.metadata,
        "warnings": panel.warnings,
        "execution_seconds": time.perf_counter() - started,
        "windows": records,
        "summary": {
            key: summary[key]
            for key in ("valid_count", "failed_count", "return_shortfall", "excess_volatility")
        },
        "reproducibility": {
            **provenance,
            "calculation_sha256": hashlib.sha256(
                json.dumps(identity, sort_keys=True).encode()
            ).hexdigest(),
            "user_weights_sha256": hashlib.sha256(
                json.dumps(identity, sort_keys=True).encode()
            ).hexdigest(),
        },
    }
