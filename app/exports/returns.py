import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only, sessionmaker

from app.db.models import Instrument, MonthlyReturn, ReturnSeries


def export_returns(
    factory: sessionmaker[Session],
    path: Path,
    *,
    format: str = "parquet",
    wide: bool = False,
    currency: str | None = None,
    symbol: str | None = None,
    source: str = "lazyportfolioetf",
) -> Path:
    if format not in {"csv", "parquet"}:
        raise ValueError("Export format must be csv or parquet")
    if wide and currency is None:
        raise ValueError("Wide export requires an explicit common --currency")
    if currency:
        currency = currency.upper()
    with factory() as session:
        query = (
            select(MonthlyReturn, ReturnSeries, Instrument)
            .options(
                load_only(
                    ReturnSeries.id,
                    ReturnSeries.source,
                    ReturnSeries.currency,
                    ReturnSeries.extraction_method,
                    ReturnSeries.currency_kind,
                ),
                load_only(Instrument.id, Instrument.source_symbol),
            )
            .join(ReturnSeries, MonthlyReturn.series_id == ReturnSeries.id)
            .join(Instrument, Instrument.id == ReturnSeries.instrument_id)
            .where(
                ReturnSeries.source == source,
                ReturnSeries.frequency == "monthly",
                ReturnSeries.return_type == "nominal_total_return",
            )
            .order_by(MonthlyReturn.date, Instrument.source_symbol)
        )
        if currency:
            query = query.where(ReturnSeries.currency == currency)
        if symbol:
            query = query.where(Instrument.source_symbol == symbol)
        results = list(session.execute(query))
    if not results:
        raise ValueError("No imported observations match the export filters")
    rows: list[dict[str, Any]] = []
    if wide:
        tickers = sorted({instrument.source_symbol for _, _, instrument in results})
        matrix: dict = {}
        for observation, _, instrument in results:
            row = matrix.setdefault(observation.date, {"date": observation.date})
            if instrument.source_symbol in row:
                raise ValueError("Ambiguous multiple series for a wide-matrix ticker")
            row[instrument.source_symbol] = observation.return_value
        rows = [
            {"date": day, **{ticker: values.get(ticker) for ticker in tickers}}
            for day, values in sorted(matrix.items())
        ]
        fields = ["date", *tickers]
    else:
        fields = [
            "date",
            "ticker",
            "currency",
            "extraction_method",
            "currency_kind",
            "return",
            "instrument_id",
            "series_id",
            "source",
            "quality_flag",
            "extracted_at",
        ]
        rows = [
            {
                "date": observation.date,
                "ticker": instrument.source_symbol,
                "currency": series.currency,
                "extraction_method": series.extraction_method,
                "currency_kind": series.currency_kind,
                "return": observation.return_value,
                "instrument_id": instrument.id,
                "series_id": series.id,
                "source": series.source,
                "quality_flag": observation.quality_flag,
                "extracted_at": observation.source_metadata.get("extracted_at"),
            }
            for observation, series, instrument in results
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as temporary:
        temp = Path(temporary.name)
    try:
        if format == "csv":
            with temp.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        else:
            arrays = {}
            for field in fields:
                values = [row[field] for row in rows]
                if field == "date":
                    arrays[field] = pa.array(values, type=pa.date32())
                elif field == "return" or (wide and field != "date"):
                    # Float64 columns load directly into Pandas/NumPy. Exact source decimals
                    # remain in the database and CSV. Conversion adds no source precision.
                    arrays[field] = pa.array(
                        [float(v) if v is not None else None for v in values], type=pa.float64()
                    )
                else:
                    arrays[field] = pa.array(values)
            table = pa.table(arrays)
            table = table.replace_schema_metadata(
                {
                    b"qcross.source": source.encode(),
                    b"qcross.currency": (
                        currency or "per-row denomination; see currency_kind"
                    ).encode(),
                    b"qcross.return_type": b"nominal_total_return; decimal fractions",
                    b"qcross.missing_values": b"null; no zero fill; union of available months",
                    b"qcross.precision": (
                        b"source-rounded; export float64; exact decimals in CSV/DB"
                    ),
                }
            )
            pq.write_table(table, temp, compression="zstd")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    metadata = {
        "created_at": datetime.now(UTC).isoformat(),
        "format": format,
        "layout": "wide" if wide else "long",
        "source": source,
        "currency": currency or "per-row denomination; see currency_kind",
        "series_methods": {
            str(s.id): {
                "ticker": i.source_symbol,
                "currency": s.currency,
                "extraction_method": s.extraction_method,
                "currency_kind": s.currency_kind,
            }
            for _, s, i in results
        },
        "rows": len(rows),
        "missing_values": "Union of available months; null/empty, never zero-filled",
        "numeric_storage": "float64" if format == "parquet" else "exact decimal text",
        "instruments": sorted({i.source_symbol for _, _, i in results}),
    }
    path.with_suffix(path.suffix + ".metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return path
