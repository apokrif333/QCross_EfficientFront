import numpy as np
import pytest

from app.analytics.cross_validation import analyze_cross_validation, portfolio_folds
from app.analytics.models import AnalyticsError


def test_classical_fold_construction_and_minimum():
    splits = portfolio_folds(151, 5)
    seen = []
    for train, validation in splits:
        assert len(train) >= 120 and not set(train) & set(validation)
        assert len(train) + len(validation) == 151
        assert np.all(np.diff(validation) == 1)
        seen.extend(validation.tolist())
    assert seen == list(range(151))
    assert [len(v) for _, v in splits] == [31, 30, 30, 30, 30]
    assert len(portfolio_folds(150, 5)) == 5
    with pytest.raises(AnalyticsError, match="only 119 training"):
        portfolio_folds(149, 5)
    for k in [1, 11]:
        with pytest.raises(AnalyticsError, match="2 to 10"):
            portfolio_folds(240, k)


@pytest.mark.parametrize("objective", ["gmv", "max_sharpe", "target_return"])
def test_averaging_constraints_and_validation_metrics(panel, objective):
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

    def spy(values, folds=5, target="identity"):
        observed.append(values.copy())
        return actual(values, folds, target)

    monkeypatch.setattr(estimators, "select_shrinkage", spy)
    output = analyze_cross_validation(
        panel, covariance_method="cv_linear", shrinkage_target="diagonal"
    )
    assert output["status"] == "complete" and len(observed) == 6
    np.testing.assert_array_equal(observed[0], panel.values)
    for training_values, (training, _) in zip(observed[1:], portfolio_folds(240), strict=True):
        np.testing.assert_array_equal(training_values, panel.values[training])
    # Perturb only outer fold one's validation. Its selected model/weights must stay unchanged.
    changed = panel.returns.copy()
    changed.iloc[:48] += 0.2
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


def test_infeasible_target_is_not_replaced_or_partially_averaged(panel):
    output = analyze_cross_validation(panel, objective="target_return", target_return=1.0)
    assert output["status"] == "incomplete" and output["ensemble"] is None
    assert all(fold["status"] == "infeasible" for fold in output["folds"])
