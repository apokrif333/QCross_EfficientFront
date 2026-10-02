import numpy as np
import pandas as pd
import pytest

from app.analytics.models import ReturnPanel


@pytest.fixture
def panel() -> ReturnPanel:
    rng = np.random.default_rng(42)
    values = rng.normal([0.009, 0.005, 0.006], [0.04, 0.02, 0.03], (240, 3))
    frame = pd.DataFrame(
        values, index=pd.period_range("2000-01", periods=240, freq="M"), columns=["1", "2", "3"]
    )
    return ReturnPanel(frame, list(frame.columns), {"Equity": ["1", "3"], "US Stocks": ["1"]})
