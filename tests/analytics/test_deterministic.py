from datetime import UTC, date, datetime
from decimal import Decimal

import nonlinshrink
import numpy as np
import pytest
from scipy.optimize import minimize_scalar
from scipy.stats import multivariate_normal
from sklearn.covariance import LedoitWolf
from sklearn.model_selection import KFold

from app.analytics.constraints import build_constraints
from app.analytics.data_loader import align_returns, load_returns
from app.analytics.estimators import (
    estimate_covariance,
    gaussian_nll,
    linear_covariance,
    nonlinear_covariance,
    select_shrinkage,
    validate_covariance,
)
from app.analytics.frontier import analyze_frontier
from app.analytics.metrics import correlation_diagnostics, historical_metrics, portfolio_metrics
from app.analytics.models import AnalyticsError
from app.analytics.optimizer import PortfolioOptimizer
from app.db.models import Instrument, MonthlyReturn, ReturnSeries


def test_alignment_intersection_and_requested_range(panel):
    series = {a: panel.returns[a].copy() for a in panel.assets}
    series["1"] = series["1"].iloc[10:]
    series["2"] = series["2"].iloc[:-5]
    aligned = align_returns(series)
    assert len(aligned) == 225
    assert str(aligned.index[0]) == "2000-11" and str(aligned.index[-1]) == "2019-07"
    selected = align_returns(series, start_date=date(2002, 1, 1), end_date=date(2015, 12, 31))
    assert len(selected) == 168
    series["3"] = series["3"].drop(series["3"].index[100])
    with pytest.raises(AnalyticsError, match="missing months for 3"):
        align_returns(series)
    with pytest.raises(AnalyticsError, match="only 94 months"):
        align_returns({a: panel.returns[a].iloc[:94] for a in panel.assets})


def test_covariances_and_independent_nonlinear_reference(panel):
    x = panel.values
    np.testing.assert_allclose(estimate_covariance(x).matrix, np.cov(x, rowvar=False, ddof=1))
    lw = estimate_covariance(x, "ledoit_wolf")
    np.testing.assert_allclose(lw.matrix, LedoitWolf().fit(x).covariance_)
    np.testing.assert_allclose(nonlinear_covariance(x), nonlinshrink.shrink_cov(x), rtol=1e-11)
    # Independent oracle across very different spectra and repeated eigenvalues.
    for seed in range(4):
        z = np.random.default_rng(seed).normal(size=(150, 8)) * np.geomspace(0.01, 0.2, 8)
        np.testing.assert_allclose(nonlinear_covariance(z), nonlinshrink.shrink_cov(z), rtol=1e-10)
    assert lw.parameters["target"] == "identity"


@pytest.mark.parametrize("target", ["identity", "diagonal"])
def test_cv_shrinkage_uses_validation_nll(panel, target):
    chosen = select_shrinkage(panel.values, 5, target)
    scores = []
    for intensity in chosen["grid"]:
        total = 0
        for train, validation in KFold(5, shuffle=False).split(panel.values):
            covariance, _ = validate_covariance(
                linear_covariance(panel.values[train], intensity, target)
            )
            total += len(validation) * gaussian_nll(
                panel.values[validation], panel.values[train].mean(0), covariance
            )
        scores.append(total / len(panel.values))
    assert chosen["intensity"] == chosen["grid"][int(np.argmin(scores))]
    np.testing.assert_allclose(chosen["nll_scores"], scores)
    diagonal = linear_covariance(panel.values, 1, "diagonal")
    np.testing.assert_allclose(np.diag(diagonal), np.var(panel.values, axis=0, ddof=1))


def test_annualization_compounding_and_drawdown(panel):
    w = np.ones(3) / 3
    mean, covariance = panel.values.mean(0) * 12, np.cov(panel.values, rowvar=False) * 12
    stats = portfolio_metrics(w, panel.values, mean, covariance)
    assert stats["expected_return"] == pytest.approx(np.mean(panel.values @ w) * 12)
    assert stats["volatility"] == pytest.approx(np.std(panel.values @ w, ddof=1) * np.sqrt(12))
    assert stats["historical_cagr"] == pytest.approx(
        np.prod(1 + panel.values @ w) ** (12 / 240) - 1
    )
    assert historical_metrics(np.array([-0.2, 0.1]))["historical_max_drawdown"] == pytest.approx(
        0.2
    )
    assert historical_metrics(np.array([-1.0, 0.1]))["historical_cagr"] == -1


def test_gmv_and_sharpe_against_analytical_solutions():
    mean = np.array([0.10, 0.08])
    covariance = np.diag([0.04, 0.01])
    rules = build_constraints(["A", "B"])
    engine = PortfolioOptimizer(mean, covariance, rules)
    np.testing.assert_allclose(engine.gmv().require_weights(), [0.2, 0.8], atol=1e-7)
    analytic = np.linalg.solve(covariance, mean - 0.03)
    analytic /= analytic.sum()
    np.testing.assert_allclose(engine.max_sharpe().require_weights(), analytic, atol=1e-7)
    bounded = build_constraints(["A", "B"], {"B": {"max": 0.6}})
    weights = PortfolioOptimizer(mean, covariance, bounded).max_sharpe().require_weights()
    oracle = minimize_scalar(
        lambda a: (
            -(
                (a * mean[0] + (1 - a) * mean[1] - 0.03)
                / np.sqrt(a * a * 0.04 + (1 - a) ** 2 * 0.01)
            )
        ),
        bounds=(0.4, 1),
        method="bounded",
    )
    assert weights[0] == pytest.approx(oracle.x, abs=1e-5)
    assert engine.target_return(0.2).status == "infeasible"


def test_nested_constraints_and_infeasibility(panel):
    bounds = {"Equity": {"min": 0.2, "max": 0.7}, "US Stocks": {"max": 0.4}}
    result = analyze_frontier(
        panel, group_constraints=bounds, asset_constraints={"2": {"min": 0.4}}
    )
    assert result["status"] == "complete"
    assert result["equal_weight"]["feasible"] is False
    for point in result["frontier"] + [result["gmv"], result["max_sharpe"]]:
        w = point["weights"]
        assert sum(w.values()) == pytest.approx(1)
        assert w["2"] >= 0.4 - 1e-7 and w["1"] <= 0.4 + 1e-7
        assert 0.2 - 1e-7 <= w["1"] + w["3"] <= 0.7 + 1e-7
    returns = [p["metrics"]["expected_return"] for p in result["frontier"]]
    volatility = [p["metrics"]["volatility"] for p in result["frontier"]]
    assert np.all(np.diff(returns) >= -1e-7) and np.all(np.diff(volatility) >= -1e-7)
    assert result["frontier"][0]["metrics"]["volatility"] == pytest.approx(
        result["gmv"]["metrics"]["volatility"]
    )
    for assets, groups in [
        ({"1": {"min": 0.8}, "2": {"min": 0.8}}, {}),
        ({}, {"Equity": {"max": 0.2}, "US Stocks": {"min": 0.4}}),
    ]:
        with pytest.raises(AnalyticsError, match="infeasible"):
            build_constraints(panel.assets, assets, groups, panel.groups)
    with pytest.raises(AnalyticsError, match="unselected"):
        build_constraints(panel.assets, {"unknown": {"max": 0.4}})
    with pytest.raises(AnalyticsError, match="0 <= min"):
        build_constraints(panel.assets, {"1": {"min": -0.1}})


def test_pathological_covariance_is_explicit(panel):
    identical = np.repeat(panel.values[:, :1], 3, axis=1)
    estimate = estimate_covariance(identical)
    assert estimate.diagnostics["regularization"] == "eigenvalue_floor"
    assert estimate.diagnostics["frobenius_change"] > 0
    assert estimate.diagnostics["condition_number_after"] <= 1.01e10
    with pytest.raises(AnalyticsError, match="singular"):
        estimate_covariance(identical, "nonlinear")
    zero = panel.values.copy()
    zero[:, 0] = 0
    assert correlation_diagnostics(zero, panel.assets)["matrix"][0][0] is None
    assert estimate_covariance(zero).diagnostics["nearly_singular"]
    for bad in [np.array([[1, 2], [2, 1]]), np.array([[1, 2], [0, 1]]), np.full((2, 2), np.nan)]:
        with pytest.raises(AnalyticsError):
            validate_covariance(bad)
    result = PortfolioOptimizer(
        np.array([0.01, 0.02]), np.eye(2), build_constraints(["A", "B"])
    ).max_sharpe()
    assert result.status == "optimal"
    np.testing.assert_allclose(result.require_weights(), [0, 1])


def seed_database(session_factory, panel):
    with session_factory.begin() as session:
        for i, asset in enumerate(panel.assets, 1):
            session.add(
                Instrument(
                    id=i,
                    source="test",
                    source_symbol=asset,
                    ticker=asset,
                    category="US Stocks",
                    raw_metadata={},
                )
            )
        session.flush()
        for i, asset in enumerate(panel.assets, 1):
            months = panel.returns.index.to_timestamp(how="end").date
            session.add(
                ReturnSeries(
                    id=i,
                    instrument_id=i,
                    source="test",
                    currency="USD",
                    validation_status="validated",
                    start_date=months[0],
                    end_date=months[-1],
                    observation_count=len(months),
                    last_updated_at=datetime.now(UTC),
                    source_metadata={},
                )
            )
            session.flush()
            session.add_all(
                [
                    MonthlyReturn(
                        series_id=i,
                        date=month,
                        return_value=Decimal(str(value)),
                        quality_flag="synthetic_test",
                        source_metadata={},
                    )
                    for month, value in zip(months, panel.returns[asset], strict=True)
                ]
            )


def test_database_validation_currency_and_no_writes(session_factory, panel):
    seed_database(session_factory, panel)
    with session_factory() as session:
        loaded = load_returns(session, [3, 1, 2], today=date(2020, 1, 1))
        assert loaded.assets == ["3", "1", "2"] and len(loaded.returns) == 240
        assert loaded.groups["Equity"] == ["3", "1", "2"]
        assert not session.dirty and not session.new
        with pytest.raises(AnalyticsError, match="Only USD"):
            load_returns(session, [1, 2], currency="EUR")
        with pytest.raises(AnalyticsError, match="maximum of 15"):
            load_returns(session, list(range(16)))
        session.get(ReturnSeries, 1).validation_status = "failed"
        with pytest.raises(AnalyticsError, match="validated USD"):
            load_returns(session, [1, 2])


def test_nll_against_scipy_reference(panel):
    train, validation = panel.values[:180], panel.values[180:]
    covariance = linear_covariance(train, 0.3, "identity")
    expected = -multivariate_normal.logpdf(validation, mean=train.mean(0), cov=covariance).mean()
    assert gaussian_nll(validation, train.mean(0), covariance) == pytest.approx(expected)


def test_loader_shared_proxy_requires_metadata_evidence(session_factory, panel):
    seed_database(session_factory, panel)
    with session_factory.begin() as session:
        for instrument_id in [1, 2]:
            series = session.get(ReturnSeries, instrument_id)
            series.source_metadata = {"shared_historical_proxy_id": "documented-proxy"}
            series.is_extended_history = True
        loaded = load_returns(session, [1, 2])
        assert any("establishes shared" in warning for warning in loaded.warnings)
        for instrument_id in [1, 2]:
            session.get(ReturnSeries, instrument_id).source_metadata = {}
        loaded = load_returns(session, [1, 2])
        assert not any("establishes shared" in warning for warning in loaded.warnings)


def test_risk_aversion_against_analytical_interior_solution():
    mean, covariance = np.array([0.08, 0.10]), np.diag([0.04, 0.09])
    gamma = 5
    # Differentiate 0.08*w+0.10*(1-w)-5*(0.04*w^2+0.09*(1-w)^2).
    expected_a = (mean[0] - mean[1] + 2 * gamma * 0.09) / (2 * gamma * (0.04 + 0.09))
    weights = (
        PortfolioOptimizer(mean, covariance, build_constraints(["A", "B"]))
        .utility(1 / gamma)
        .require_weights()
    )
    np.testing.assert_allclose(weights, [expected_a, 1 - expected_a], atol=1e-7)


def test_negative_sharpe_with_overlapping_group_bounds():
    rules = build_constraints(
        ["A", "B"], group_bounds={"A only": {"max": 0.4}}, memberships={"A only": ["A"]}
    )
    result = PortfolioOptimizer(np.array([0.01, 0.02]), np.eye(2), rules).max_sharpe()
    assert result.status == "optimal"
    np.testing.assert_allclose(result.require_weights(), [0, 1], atol=1e-7)
