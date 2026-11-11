"""Synthetic checks for the local, read-only historical-return reconciliation."""

import numpy as np
import pandas as pd
import pytest

from scripts.audit_tiingo_returns import load_prices, statistics


def test_actual_last_date_and_joint_dividend_split_aggregation(tmp_path):
    path = tmp_path / "TEST.csv"
    pd.DataFrame(
        {
            "date": ["2020-02-28", "2020-01-31", "2020-02-03"],
            "adjClose": [110, 100, 105],
            "close": [55, 100, 52.5],
            "divCash": [0.5, 0, 1],
            "splitFactor": [1, 1, 2],
        }
    ).to_csv(path, index=False)
    original = path.read_bytes()
    monthly, identity = load_prices(path)
    assert monthly.index.astype(str).tolist() == ["2020-01", "2020-02"]
    assert monthly.iloc[-1].adjClose == 110
    assert monthly.iloc[-1].divCash == 1.5
    assert monthly.iloc[-1].splitFactor == 2
    assert identity["price_last_date"] == "2020-02-28"
    assert path.read_bytes() == original


def test_duplicate_daily_date_rejected(tmp_path):
    path = tmp_path / "TEST.csv"
    pd.DataFrame(
        {
            "date": ["2020-01-31"] * 2,
            "adjClose": [100, 101],
            "close": [100, 101],
            "divCash": [0, 0],
            "splitFactor": [1, 1],
        }
    ).to_csv(path, index=False)
    with pytest.raises(ValueError, match="Duplicate"):
        load_prices(path)


def test_cagr_compounding_and_percentage_point_threshold():
    frame = pd.DataFrame(
        {"lazy_return": [0.10, -0.05], "tiingo_return": [0.095, -0.05], "difference_pp": [0.5, 0]},
        index=pd.period_range("2020-01", periods=2, freq="M"),
    )
    stats = statistics(frame, 0.5)
    assert stats["months_over_threshold"] == 1
    assert stats["lazy_cagr_pct"] == pytest.approx(((1.1 * 0.95) ** 6 - 1) * 100)
    assert stats["relative_terminal_wealth_difference_pct"] == pytest.approx(
        (1.1 / 1.095 - 1) * 100
    )
    assert stats["lazy_volatility_pct"] == pytest.approx(np.std([0.1, -0.05]) * np.sqrt(12) * 100)


def test_internal_gap_not_compounded_or_filled():
    frame = pd.DataFrame(
        {"lazy_return": [0.1, 0.2], "tiingo_return": [0.1, 0.2], "difference_pp": [0, 0]},
        index=pd.PeriodIndex(["2020-01", "2020-03"], freq="M"),
    )
    stats = statistics(frame, 0.5)
    assert not stats["contiguous"]
    assert stats["lazy_cagr_pct"] is None
    assert stats["relative_terminal_wealth_difference_pct"] is None
