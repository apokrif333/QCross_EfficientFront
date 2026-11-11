"""Read-only reconciliation of arithmetic return, CAGR and volatility for stored USD rows.

Run: .venv/Scripts/python.exe -m scripts.diagnose_return_conventions
This single-asset diagnostic does not relax the frontier's two-asset minimum.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.analytics.metrics import historical_metrics
from app.config import get_settings
from app.db.models import Instrument, MonthlyReturn, ReturnSeries
from app.db.session import make_engine, make_session_factory


def main() -> None:
    settings = get_settings()
    database = make_url(settings.database_url).database

    def digest():
        with Path(database).open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()

    before = digest()
    engine = make_engine(settings.database_url)
    cases = []
    try:
        with make_session_factory(engine)() as session:
            for ticker, end in [
                ("VTI", date(2026, 8, 31)),
                ("IJS", date(2026, 8, 31)),
                ("IJS", date(2026, 9, 30)),
            ]:
                series = session.scalars(
                    select(ReturnSeries)
                    .join(Instrument)
                    .where(
                        Instrument.ticker == ticker,
                        Instrument.source == "lazyportfolioetf",
                        ReturnSeries.currency == "USD",
                        ReturnSeries.validation_status == "validated",
                        ReturnSeries.frequency == "monthly",
                        ReturnSeries.return_type == "nominal_total_return",
                    )
                ).one()
                rows = session.execute(
                    select(MonthlyReturn.date, MonthlyReturn.return_value)
                    .where(
                        MonthlyReturn.series_id == series.id,
                        MonthlyReturn.date <= end,
                    )
                    .order_by(MonthlyReturn.date)
                ).all()
                index = pd.PeriodIndex([row.date for row in rows], freq="M")
                if not index.equals(pd.period_range(index[0], index[-1], freq="M")):
                    raise ValueError(f"Missing or duplicate months in {ticker}.")
                values = np.array([float(row.return_value) for row in rows])
                cases.append(
                    {
                        "ticker": ticker,
                        "series_id": series.id,
                        "requested_end": str(end),
                        "first_return_month": str(index[0]),
                        "last_return_month": str(index[-1]),
                        "observations": len(values),
                        "arithmetic_annual_return": float(values.mean() * 12),
                        **historical_metrics(values),
                        "historical_volatility_ddof_0": float(values.std(ddof=0) * np.sqrt(12)),
                        "sample_volatility_ddof_1": float(values.std(ddof=1) * np.sqrt(12)),
                        "stored_series_end": str(series.end_date),
                        "returns_sha256_float64_le": hashlib.sha256(
                            values.astype("<f8").tobytes()
                        ).hexdigest(),
                    }
                )
    finally:
        engine.dispose()
    after = digest()
    if before != after:
        raise RuntimeError("Database bytes changed during a read-only diagnostic.")
    result = {
        "currency": "USD",
        "cases": cases,
        "database_sha256": after,
        "database_unchanged": True,
    }
    path = Path("docs/return-conventions-check.json")
    path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
