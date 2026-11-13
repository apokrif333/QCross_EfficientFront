"""Read hedge returns once; keep them outside the optimizer universe."""

import hashlib
from copy import deepcopy

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.analytics.data_loader import load_returns
from app.analytics.models import AnalyticsError
from app.db.models import MonthlyReturn
from app.ingestion.lazyportfolio.macro_service import bil_series


def load_control_panel(session, panel, config):
    if config["mode"] == "none":
        return panel
    config = deepcopy(config)
    bil = None
    if "BIL_REFERENCE" in config["hedge_weights"]:
        try:
            bil = bil_series(session)
        except ValueError as exc:
            raise AnalyticsError(str(exc)) from exc
        key = str(bil.instrument_id)
        amount = config["hedge_weights"].pop("BIL_REFERENCE")
        config["hedge_weights"][key] = config["hedge_weights"].get(key, 0) + amount
    series = {a: panel.returns[a] for a in panel.assets}
    metadata = list(panel.metadata)
    original_months = len(panel.values)
    for asset in config["hedge_weights"]:
        if asset in series:
            continue
        if bil is not None and asset == str(bil.instrument_id):
            rows = list(
                session.scalars(select(MonthlyReturn).where(MonthlyReturn.series_id == bil.id))
            )
            series[asset] = pd.Series(
                [float(r.return_value) for r in rows],
                index=pd.PeriodIndex([r.date for r in rows], freq="M"),
            )
            metadata.append(
                {
                    "instrument_id": bil.instrument_id,
                    "ticker": "BIL",
                    "series_id": bil.id,
                    "source": bil.source,
                    "series_sha256": hashlib.sha256(
                        series[asset].to_numpy(dtype="<f8").tobytes()
                    ).hexdigest(),
                }
            )
        else:
            try:
                hedge = load_returns(session, [int(panel.assets[0]), int(asset)])
            except (ValueError, TypeError) as exc:
                raise AnalyticsError(f"Invalid hedge instrument {asset}: {exc}") from exc
            series[asset] = hedge.returns[asset]
            metadata.extend(m for m in hedge.metadata if str(m["instrument_id"]) == asset)
    start = max(s.index.min() for s in series.values())
    end = min(s.index.max() for s in series.values())
    months = pd.period_range(start, end, freq="M")
    frame = pd.DataFrame({a: s.reindex(months) for a, s in series.items()})
    if not np.isfinite(frame.to_numpy()).all() or (frame < -1).any().any() or len(frame) < 120:
        raise AnalyticsError("Original and hedge assets require 120 common months without gaps.")
    panel.returns = frame[panel.assets].copy()
    panel.control_returns = frame
    panel.control_config, panel.control_metadata = config, metadata
    if len(frame) != original_months:
        panel.warnings.append(
            f"Hedge alignment shortened history from {original_months} to {len(frame)} months."
        )
    return panel


def transform_control_panel(panel):
    if panel.control_returns is None:
        return
    if panel.return_basis == "real":
        rates = np.array([r[1] for r in panel.reference_metadata["inflation_observations"]])
        panel.control_returns = (1 + panel.control_returns).div(1 + rates, axis=0) - 1
    panel.reference_metadata["portfolio_controls"] = {
        "config": panel.control_config,
        "sources": panel.control_metadata,
        "matrix_sha256": hashlib.sha256(
            panel.control_returns.to_numpy(dtype="<f8").tobytes()
        ).hexdigest(),
        "assets": list(panel.control_returns.columns),
    }
