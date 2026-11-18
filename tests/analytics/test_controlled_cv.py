from dataclasses import replace

import numpy as np
import pytest

from app.analytics.constraints import build_constraints
from app.analytics.cross_validation import analyze_cross_validation, portfolio_folds
from app.analytics.optimizer import PortfolioOptimizer
from app.analytics.portfolio_controls import compare_controls


@pytest.mark.parametrize("mode", ["downside_vol", "trend"])
@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
def test_controlled_train_budget_and_validation_comparator(panel, mode, method):
    config = {
        "mode": mode,
        "hedge_weights": {"2": 1},
        "downside_vol": {"target_annual": 0.06, "lookback_months": 3},
        "trend": {
            "destination": "hedge",
            "asset_rules": {"1": {"indicator": "momentum", "lookback_months": 3}},
        },
    }
    user = {"1": 0.6, "2": 0.4}
    p = replace(panel, control_config=config, control_returns=panel.returns)
    result = analyze_cross_validation(
        p,
        user_weights=user,
        objective="max_return_at_user_risk",
        covariance_method=method,
        transaction_fee_rate=0.001,
    )
    assert any(f["paired_comparison"].get("optimized") for f in result["folds"])
    assert result["paired_comparison"]["user_portfolio_mode"] == "controlled"
    assert {f["training_direction"] for f in result["folds"]} == {
        "train_before_validation",
        "train_after_validation",
    }
    for fold, (train, valid) in zip(result["folds"], portfolio_folds(len(p.values)), strict=True):
        expected = compare_controls(p, user, train, record_history=False)["controlled"]["net"][
            "annualized_volatility"
        ]
        assert fold["diagnostics"]["training_user_volatility"] == pytest.approx(expected)
        assert fold["paired_comparison"]["diagnostics"][
            "training_user_volatility"
        ] == pytest.approx(expected)
        controlled = compare_controls(p, user, valid)["controlled"]["net"]
        for key in [
            "annualized_cagr",
            "annualized_volatility",
            "historical_sharpe_ratio",
            "maximum_drawdown",
        ]:
            assert fold["paired_comparison"]["user"][key] == pytest.approx(controlled[key])
        pair = fold["paired_comparison"]
        if pair["optimized"]:
            assert pair["differences"]["delta_cagr"] == pytest.approx(
                pair["optimized"]["annualized_cagr"] - controlled["annualized_cagr"]
            )
        if fold["weights"] is not None:
            from app.analytics.estimators import estimate_covariance

            matrix = (
                estimate_covariance(p.values[train], method, folds=4, target="identity").matrix * 12
            )
            weights = np.array([fold["weights"][a] for a in p.assets])
            assert np.sqrt(weights @ matrix @ weights) <= expected + 1e-7


def test_controlled_train_has_no_validation_leakage(panel):
    cfg = {
        "mode": "downside_vol",
        "hedge_weights": {"2": 1},
        "downside_vol": {"target_annual": 0.06, "lookback_months": 3},
    }
    user = {"1": 0.6, "2": 0.4}
    original = analyze_cross_validation(
        replace(panel, control_config=cfg, control_returns=panel.returns),
        user_weights=user,
        objective="max_return_at_user_risk",
    )
    frame = panel.returns.copy()
    frame.iloc[:60, 0] += 0.04
    revised = analyze_cross_validation(
        replace(panel, returns=frame, control_config=cfg, control_returns=frame),
        user_weights=user,
        objective="max_return_at_user_risk",
    )
    a, b = original["folds"][0], revised["folds"][0]
    assert a["weights"] == b["weights"]
    assert a["paired_comparison"]["weights"] == b["paired_comparison"]["weights"]
    assert (
        a["diagnostics"]["training_user_volatility"] == b["diagnostics"]["training_user_volatility"]
    )
    assert (
        a["paired_comparison"]["user"]["annualized_cagr"]
        != b["paired_comparison"]["user"]["annualized_cagr"]
    )


def test_unattainable_controlled_risk_is_explicit():
    constraints = build_constraints(["1", "2"])
    engine = PortfolioOptimizer(np.array([0.1, 0.2]), np.eye(2) * 0.04, constraints)
    result = engine.max_return_at_user_risk([0.5, 0.5], training_volatility=0.01)
    assert result.status == "risk_infeasible"
    assert result.weights is None
    assert result.diagnostics["minimum_achievable_volatility"] > 0.01
    feasible = engine.max_return_at_user_risk([0.5, 0.5], training_volatility=0.16)
    assert feasible.status == "optimal"
    assert feasible.weights[1] == pytest.approx((1 + np.sqrt(0.28)) / 2, abs=1e-5)
