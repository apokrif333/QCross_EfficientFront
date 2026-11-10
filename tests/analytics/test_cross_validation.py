import numpy as np
import pytest

from app.analytics.cross_validation import analyze_cross_validation, portfolio_folds
from app.analytics.models import AnalyticsError


def test_one_sided_portfolio_metadata(panel):
    output = analyze_cross_validation(panel)
    assert output["methodology"] == "bidirectional_one_sided_block_kfold"
    assert output["validation_observations"] == 240
    assert output["validation_coverage_fraction"] == 1
    assert [f["training"]["observations"] for f in output["folds"]] == [180, 120, 120, 180]
    assert [f["training_direction"] for f in output["folds"]] == [
        "train_after_validation",
        "train_after_validation",
        "train_before_validation",
        "train_before_validation",
    ]
    assert all(len(f["training"]["segments"]) == 1 for f in output["folds"])


@pytest.mark.parametrize("objective", ["gmv", "max_sharpe", "target_return"])
def test_averaging_constraints_and_validation_metrics(panel, objective):
    if objective == "target_return":
        # The original random fixture's one-sided mean ranges do not share a
        # feasible target. Center each synthetic block to the same mean for this
        # success case; infeasible targets retain their separate failure tests.
        from app.analytics.models import ReturnPanel

        returns = panel.returns.copy()
        mean = returns.mean()
        for start in range(0, 240, 60):
            block = returns.iloc[start : start + 60]
            returns.iloc[start : start + 60] = block - block.mean() + mean
        panel = ReturnPanel(returns, panel.assets, panel.groups)
    options = {
        "objective": objective,
        "target_return": 0.055 if objective == "target_return" else None,
        "group_constraints": {"Equity": {"max": 0.7}, "US Stocks": {"max": 0.4}},
    }
    output = analyze_cross_validation(panel, **options)
    assert output["status"] == "complete"
    weights = np.array([[f["weights"][a] for a in panel.assets] for f in output["folds"]])
    averaged = np.array([output["ensemble"]["weights"][a] for a in panel.assets])
    np.testing.assert_allclose(averaged, weights.mean(0))
    assert averaged.sum() == pytest.approx(1)
    assert averaged[0] + averaged[2] <= 0.7 + 1e-7 and averaged[0] <= 0.4 + 1e-7
    np.testing.assert_allclose(
        list(output["stability"]["standard_deviation"].values()), weights.std(0)
    )
    for fold, (_, validation) in zip(output["folds"], portfolio_folds(240), strict=True):
        w = np.array([fold["weights"][a] for a in panel.assets])
        realized = panel.values[validation] @ w
        assert fold["validation_metrics"]["realized_annual_arithmetic_return"] == pytest.approx(
            realized.mean() * 12
        )
        assert fold["validation_metrics"]["realized_annual_volatility"] == pytest.approx(
            realized.std(ddof=1) * np.sqrt(12)
        )
    repeated = analyze_cross_validation(panel, **options)
    assert repeated["ensemble"]["weights"] == output["ensemble"]["weights"]
    assert "not_independent_out_of_sample" in output["ensemble_evaluation"]


def test_nested_shrinkage_selection_has_no_validation_leakage(panel, monkeypatch):
    import app.analytics.estimators as estimators

    actual, observed = estimators.select_shrinkage, []
    actual_splitter, inner_samples = estimators.one_sided_folds, []

    def split_spy(observations, folds=4, **options):
        splits = actual_splitter(observations, folds, **options)
        inner_samples.append((observations, splits))
        return splits

    monkeypatch.setattr(estimators, "one_sided_folds", split_spy)

    def spy(values, folds=4, target="identity"):
        observed.append(values.copy())
        return actual(values, folds, target)

    monkeypatch.setattr(estimators, "select_shrinkage", spy)
    output = analyze_cross_validation(
        panel, covariance_method="cv_linear", shrinkage_target="diagonal"
    )
    assert output["status"] == "complete" and len(observed) == 5
    np.testing.assert_array_equal(observed[0], panel.values)
    for training_values, (training, _) in zip(observed[1:], portfolio_folds(240), strict=True):
        np.testing.assert_array_equal(training_values, panel.values[training])
    for (count, inner), (outer_train, outer_validation) in zip(
        inner_samples[1:], portfolio_folds(240), strict=True
    ):
        assert count == len(outer_train)
        for train, validation in inner:
            assert not np.intersect1d(outer_train[train], outer_validation).size
            assert not np.intersect1d(outer_train[validation], outer_validation).size
    # Perturb only outer fold one's validation. Its selected model/weights must stay unchanged.
    changed = panel.returns.copy()
    changed.iloc[:60] += 0.2
    from app.analytics.models import ReturnPanel

    second = analyze_cross_validation(
        ReturnPanel(changed, panel.assets, panel.groups),
        covariance_method="cv_linear",
        shrinkage_target="diagonal",
    )
    assert second["folds"][0]["weights"] == output["folds"][0]["weights"]
    assert (
        second["folds"][0]["covariance_parameters"] == output["folds"][0]["covariance_parameters"]
    )
    first, perturbed = output["folds"][0], second["folds"][0]
    assert perturbed["covariance_diagnostics"] == first["covariance_diagnostics"]
    assert perturbed["training_metrics"] == first["training_metrics"]
    assert perturbed["validation_metrics"] != first["validation_metrics"]
    assert second["annual_covariance"] != output["annual_covariance"]


def test_infeasible_target_is_not_replaced_or_partially_averaged(panel):
    output = analyze_cross_validation(panel, objective="target_return", target_return=1.0)
    assert output["status"] == "incomplete" and output["ensemble"] is None
    assert all(fold["status"] == "infeasible" for fold in output["folds"])


def test_single_failed_fold_prevents_partial_ensemble(panel, monkeypatch):
    import app.analytics.cross_validation as cv

    actual, calls = cv.estimate_covariance, []

    def fail_one(values, *args, **kwargs):
        calls.append(len(values))
        if len(calls) == 2:
            raise AnalyticsError("Forced fold failure")
        return actual(values, *args, **kwargs)

    monkeypatch.setattr(cv, "estimate_covariance", fail_one)
    output = analyze_cross_validation(panel)
    assert output["status"] == "incomplete"
    assert output["ensemble"] is None and output["stability"] is None
    assert len([f for f in output["folds"] if f["weights"] is not None]) == 3
    assert output["folds"][1]["diagnostics"]["reason"] == "Forced fold failure"


def test_k2_incomplete_validation_coverage(panel):
    from app.analytics.models import ReturnPanel

    short = ReturnPanel(panel.returns.iloc[:180], panel.assets, panel.groups)
    output = analyze_cross_validation(short, cv_folds=2, covariance_method="cv_linear")
    assert output["status"] == "complete"
    assert output["validation_observations"] == 120
    assert output["validation_coverage_fraction"] == pytest.approx(2 / 3)
    assert output["unvalidated_indices"] == list(range(60, 120))


def test_fold_weights_and_stability_match_training_optimization(panel):
    from app.analytics.constraints import build_constraints
    from app.analytics.estimators import estimate_covariance
    from app.analytics.optimizer import PortfolioOptimizer

    bounds = {"1": {"min": 0.1, "max": 0.6}}
    constraints = build_constraints(panel.assets, bounds, None, panel.groups)
    output = analyze_cross_validation(panel, asset_constraints=bounds)
    weights = []
    for fold, (train, _) in zip(output["folds"], portfolio_folds(240), strict=True):
        values = panel.values[train]
        expected = PortfolioOptimizer(
            values.mean(0) * 12, estimate_covariance(values).matrix * 12, constraints
        ).gmv()
        w = np.array([fold["weights"][a] for a in panel.assets])
        np.testing.assert_array_equal(w, expected.weights)
        weights.append(w)
    weights = np.stack(weights)
    stability = output["stability"]
    center = weights.mean(0)
    np.testing.assert_array_equal(list(stability["minimum"].values()), weights.min(0))
    np.testing.assert_array_equal(list(stability["maximum"].values()), weights.max(0))
    assert stability["mean_absolute_weight_deviation"] == pytest.approx(
        abs(weights - center).mean()
    )
    assert stability["maximum_weight_deviation"] == pytest.approx(abs(weights - center).max())
    original = np.array(list(output["original"]["weights"].values()))
    assert stability["mean_allocation_turnover_from_original"] == pytest.approx(
        abs(weights - original).sum(1).mean() / 2
    )
    for name, bound in [
        ("lower_bound_frequency", constraints.lower),
        ("upper_bound_frequency", constraints.upper),
    ]:
        np.testing.assert_array_equal(
            list(stability[name].values()), np.mean(abs(weights - bound) <= 1e-6, axis=0)
        )
