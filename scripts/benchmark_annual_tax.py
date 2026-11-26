"""Time the retained-sample Bootstrap trend sweep on deterministic inputs."""

import hashlib
from time import perf_counter

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from app.analytics.bootstrap import bootstrap_generation
from app.analytics.models import ReturnPanel
from app.analytics.portfolio_controls import analyze_bootstrap_trend_sensitivity
from app.analytics.splitting import bootstrap_folds


def main():
    months, draws = 608, 500
    assets = ["A", "B", "C", "D", "BIL"]
    rng = np.random.default_rng(2026)
    returns = rng.normal(
        [0.006, 0.005, 0.008, 0.004, 0.002], [0.04, 0.03, 0.06, 0.02, 0.001], (months, len(assets))
    )
    frame = pd.DataFrame(
        returns, columns=assets, index=pd.period_range("1974-04", periods=months, freq="M")
    )
    panel = ReturnPanel(frame, assets, control_returns=frame, risk_free_returns=returns[:, -1])
    panel.control_config = {
        "mode": "trend",
        "hedge_weights": {"BIL": 1},
        "trend": {
            "destination": "hedge",
            "asset_rules": {
                a: {"indicator": "sma" if i % 2 == 0 else "momentum", "lookback_months": 12}
                for i, a in enumerate(assets[:-1])
            },
        },
    }
    splits = bootstrap_folds(months, 4, "sample")
    indices = np.array(
        [
            np.concatenate([v for _, v in bootstrap_generation(splits, 12, 42, draw)])
            for draw in range(draws)
        ]
    )
    print(hashlib.sha256(indices.astype("<i8").tobytes()).hexdigest(), flush=True)
    with threadpool_limits(limits=1):
        for rate in (0, 0.10):
            panel.simulation_options = {
                "transaction_fee_rate": 0.002,
                "capital_gains_tax_rate": rate,
            }
            timings = []
            for _ in range(3):
                start = perf_counter()
                analyze_bootstrap_trend_sensitivity(
                    panel, user_weights={a: 0.25 for a in assets[:-1]}, _sample_indices=indices
                )
                timings.append(perf_counter() - start)
            print(f"tax={rate:.0%}: {timings}, median={np.median(timings):.3f}s", flush=True)


if __name__ == "__main__":
    main()
