import copy
import json
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.analytics.bootstrap import analyze_bootstrap, stationary_indices
from app.analytics.cross_validation import analyze_cross_validation
from app.analytics.frontier import analyze_frontier
from app.analytics.models import ReturnPanel
from app.analytics.references import real_returns
from app.ingestion.lazyportfolio.inflation import extract_inflation
from app.ingestion.lazyportfolio.returns_models import ReturnExtractionError
from app.ingestion.lazyportfolio.returns_parser import embedded_state
from tests.analytics.test_portfolio_simulator import annual_returns

SOURCE = Path("tests/fixtures/lazyportfolio_bil_inflation.html")


def extract(html, **kwargs):
    return extract_inflation(
        html,
        source_url="https://www.lazyportfolioetf.com/etf/spdr-blmbg-barclays-1-3-mth-t-bill-bil/",
        extracted_at=datetime(2026, 11, 10, tzinfo=UTC),
        **kwargs,
    )


def source_state():
    html = SOURCE.read_text(encoding="utf-8")
    state = embedded_state(html)
    portfolio = next(p for p in state["portfolios"].values() if p.get("pID") == "BIL")
    return html, state, portfolio["rend"]["sliceArray"]["MAX"]


def rewritten(state):
    # Preserve every published convention except the arrays under test.
    return (
        "<script>const _d = "
        + json.dumps(state, default=float)
        + "</script> Dividend reinvestment (without dividend taxation), when applicable"
    )


def test_actual_bil_source_coverage_and_status():
    rows, nominal, digest = extract(SOURCE.read_text(encoding="utf-8"))
    assert len(rows) == 2806 and len(nominal.capital) == 2807
    assert str(rows[0].month) == "1793-01-31" and str(rows[-1].month) == "2026-10-31"
    assert rows[0].status == "historical_reconstruction"
    assert [str(r.month)[:7] for r in rows if r.status == "assumed_zero_unpublished"] == [
        "2025-10",
        "2026-10",
    ]
    assert len(digest) == 64


def test_independent_pinned_cpi_vintages():
    reference = json.loads(Path("tests/fixtures/cpi_reference.json").read_text())
    rows, _, _ = extract(SOURCE.read_text(encoding="utf-8"))
    inflation = {str(row.month)[:7]: row.value for row in rows}
    checked = 0
    for code, series in reference["observations"].items():
        for month, value in series.items():
            year, number = map(int, month.split("-"))
            previous = f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02}"
            nsa = code == "CUUR0000SA0" and year < 1948
            sa = code == "CUSR0000SA0" and 1948 <= year <= 2019
            if previous not in series or not (nsa or sa):
                continue
            official = Decimal(value) / Decimal(series[previous]) - 1
            assert abs(official - inflation[month]) * 100 < Decimal("0.00005")
            checked += 1
    assert checked == 45


@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
def test_bil_as_selected_asset_and_zero_differential_risk(panel, method):
    panel.risk_free_returns = panel.values[:, 0].copy()
    output = analyze_frontier(panel, covariance_method=method, frontier_points=3)
    assert output["asset_portfolios"]["1"]["metrics"]["historical_sharpe_ratio"] is None
    assert output["asset_portfolios"]["1"]["metrics"]["sharpe_ratio"] is None
    assert output["status"] == "complete"


@pytest.mark.parametrize(
    "issue", ["missing", "length", "zero", "negative", "nan", "currency", "mixed"]
)
def test_inflation_rejects_bad_source(issue):
    _, state, section = source_state()
    if issue == "missing":
        del section["capitalNoInfl"]
    elif issue == "length":
        section["capitalNoInfl"].pop()
    elif issue in ["zero", "negative", "nan"]:
        section["capitalNoInfl"][1] = {"zero": 0, "negative": -1, "nan": float("nan")}[issue]
    elif issue == "currency":
        state["settings"]["currency"] = "EUR"
    else:
        next(p for p in state["portfolios"].values() if p.get("pID") == "BIL")["components"][
            "weights"
        ] = {"BIL": 50, "VTI": 50}
    with pytest.raises(ReturnExtractionError):
        extract(rewritten(state))


def test_exact_ratios_initial_capital_and_rebasing():
    _, state, section = source_state()
    section["capitalBase"][:3] = [1, 1.0045, 1.0090]
    section["capitalNoInfl"][:3] = [1, 1.0019, 1.0037]
    first, _, _ = extract(rewritten(state))
    with localcontext() as context:
        context.prec = 60
        expected = (Decimal("1.0045") / Decimal("1.0019") - 1).quantize(Decimal("1e-18"))
    assert first[0].value == expected
    section["capitalBase"] = [float(x) * 8 for x in section["capitalBase"]]
    section["capitalNoInfl"] = [float(x) * 4 for x in section["capitalNoInfl"]]
    second, _, _ = extract(rewritten(state))
    assert abs(first[0].value - second[0].value) < Decimal("1e-16")


@pytest.mark.parametrize("basis", ["nominal", "real"])
@pytest.mark.parametrize("method", ["sample", "ledoit_wolf", "nonlinear", "cv_linear"])
def test_complete_moments_and_sharpe_objective(panel, basis, method):
    rf = panel.values[:, 0] * 0.2 + np.linspace(0.001, 0.003, 240)
    values = panel.values.copy()
    if basis == "real":
        inflation = np.linspace(-0.002, 0.005, 240)
        original = values.copy()
        values = real_returns(values, inflation)
        rf = real_returns(rf, inflation)
        np.testing.assert_allclose((1 + values) * (1 + inflation[:, None]), 1 + original)
    p = ReturnPanel(
        pd.DataFrame(values, index=panel.returns.index, columns=panel.assets),
        panel.assets,
        panel.groups,
        risk_free_returns=rf,
        return_basis=basis,
        risk_free_source="historical_bil",
    )
    output = analyze_frontier(p, covariance_method=method, frontier_points=3)
    assert output["status"] == "complete"
    w = np.array(list(output["max_sharpe"]["weights"].values()))
    metrics = output["max_sharpe"]["metrics"]
    differential = annual_returns(values, w) - rf
    assert metrics["historical_sharpe_ratio"] == pytest.approx(
        np.sqrt(12) * differential.mean() / differential.std(ddof=1)
    )
    assert metrics["sharpe_ratio"] == pytest.approx(
        w
        @ p.differential["differential_mean"]
        / np.sqrt(w @ p.differential["differential_covariance"] @ w)
    )
    assert not np.allclose(p.differential["differential_covariance"], output["annual_covariance"])
    again = analyze_frontier(p, covariance_method=method, frontier_points=3)
    assert again["max_sharpe"]["weights"] == output["max_sharpe"]["weights"]


def test_nested_differential_covariance_isolation(panel):
    p = copy.deepcopy(panel)
    p.risk_free_returns = np.linspace(0.001, 0.005, 240)
    output = analyze_cross_validation(p, covariance_method="cv_linear", objective="max_sharpe")
    changed = copy.deepcopy(p)
    changed.returns.iloc[:60] += 0.1
    changed.risk_free_returns[:60] += 0.02
    second = analyze_cross_validation(
        changed, covariance_method="cv_linear", objective="max_sharpe"
    )
    assert output["folds"][0]["weights"] == second["folds"][0]["weights"]
    assert (
        output["folds"][0]["differential_covariance_parameters"]
        == second["folds"][0]["differential_covariance_parameters"]
    )
    assert output["folds"][0]["validation_metrics"] != second["folds"][0]["validation_metrics"]


def test_bootstrap_synchronizes_risk_free(panel, monkeypatch):
    import app.analytics.bootstrap as bootstrap

    p = copy.deepcopy(panel)
    p.risk_free_returns = np.linspace(0.001, 0.006, 240)
    actual = bootstrap.differential_moments
    observed = []

    def spy(values, rf, *args, **kwargs):
        observed.append((values.copy(), rf.copy()))
        return actual(values, rf, *args, **kwargs)

    monkeypatch.setattr(bootstrap, "differential_moments", spy)
    output = analyze_bootstrap(p, bootstrap_iterations=3)
    rng = np.random.default_rng(42)
    for values, rf in observed:
        indices = stationary_indices(240, 12, rng)
        np.testing.assert_array_equal(values, p.values[indices])
        np.testing.assert_array_equal(rf, p.risk_free_returns[indices])
    assert output["status"] == "complete"
