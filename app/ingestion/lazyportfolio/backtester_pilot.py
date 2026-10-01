"""Read-only comparison against existing validated individual-page observations."""

import csv
import hashlib
import json
import math
import statistics
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select

from app.db.models import Instrument, MonthlyReturn
from app.ingestion.lazyportfolio.backtester import BacktesterAdapter, monthly_csv
from app.ingestion.lazyportfolio.returns_models import ExtractedReturns, ReturnExtractionError
from app.ingestion.lazyportfolio.returns_parser import parse_returns
from app.ingestion.lazyportfolio.returns_validation import validate_returns
from app.repositories.returns import ReturnRepository
from app.storage.snapshots import save_snapshot


def export_pilot(data: ExtractedReturns, instrument_id: int, directory: Path) -> None:
    """Export validated candidate data without replacing any stored series."""
    data.require_valid()
    rows = [
        {
            "date": o.date,
            "ticker": data.source_symbol,
            "currency": data.currency,
            "return": o.return_value,
            "instrument_id": instrument_id,
            "source": data.source,
            "extraction_method": data.extraction_method,
            "currency_kind": data.currency_kind,
            "quality_flag": o.quality_flag,
        }
        for o in data.observations
    ]
    stem = directory / f"backtester_{data.source_symbol}_{data.currency}_monthly_returns"
    directory.mkdir(parents=True, exist_ok=True)
    with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    arrays = {}
    for key in rows[0]:
        values = [row[key] for row in rows]
        if key == "return":
            arrays[key] = pa.array([float(v) for v in values], type=pa.float64())
        elif key == "date":
            arrays[key] = pa.array(values, type=pa.date32())
        else:
            arrays[key] = pa.array(values)
    table = pa.table(arrays).replace_schema_metadata(
        {
            b"qcross.source": data.source.encode(),
            b"qcross.currency": data.currency.encode(),
            b"qcross.extraction_method": b"backtester",
            b"qcross.return_type": b"nominal_total_return; decimal fractions",
            b"qcross.precision": b"source-rounded; float64; exact decimals in CSV",
            b"qcross.database_overwritten": b"false",
        }
    )
    pq.write_table(table, stem.with_suffix(".parquet"), compression="zstd")
    stem.with_suffix(".metadata.json").write_text(
        json.dumps(
            {
                "snapshot_path": data.snapshot_path,
                "snapshot_sha256": data.snapshot_sha256,
                "source_url": data.source_url,
                "extracted_at": data.extracted_at.isoformat(),
                "precision_percent_decimals": data.percent_decimal_places,
                "database_overwritten": False,
                "observation_count": len(rows),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def metrics(values: list[Decimal]) -> dict[str, str]:
    capital = Decimal(1)
    for value in values:
        capital *= 1 + value
    return {
        "cumulative_capital": str(capital),
        "annualized_return": str(float(capital) ** (12 / len(values)) - 1),
        "annualized_volatility": str(statistics.pstdev(map(float, values)) * math.sqrt(12)),
    }


def compare(repository: ReturnRepository, data: ExtractedReturns, instrument: Instrument) -> dict:
    series = repository.series(instrument.id, data.currency)
    if series is None or not series.source_metadata.get("validation", {}).get("passed"):
        raise ReturnExtractionError("Pilot requires an existing validated individual-page series")
    with repository.factory() as session:
        stored = list(
            session.scalars(
                select(MonthlyReturn)
                .where(MonthlyReturn.series_id == series.id)
                .order_by(MonthlyReturn.date)
            )
        )
    old = {o.date: o.return_value for o in stored}
    new = {o.date: o.return_value for o in data.observations}
    overlap = sorted(old.keys() & new.keys())
    differences = [
        {"date": str(d), "individual_page": str(old[d]), "backtester": str(new[d])}
        for d in overlap
        if old[d] != new[d]
    ]
    validation = validate_returns(data)
    old_metrics = metrics([old[d] for d in overlap]) if overlap else {}
    new_metrics = metrics([new[d] for d in overlap]) if overlap else {}
    passed = (
        validation.passed
        and not differences
        and len(overlap) == len(old)
        and data.start_date == series.start_date
        and data.end_date >= series.end_date
        and old_metrics == new_metrics
        and data.currency_kind == "native"
        and series.extraction_method == "individual_page"
    )
    return {
        "ticker": instrument.source_symbol,
        "currency": data.currency,
        "passed": passed,
        "existing_range": [str(series.start_date), str(series.end_date)],
        "backtester_range": [str(data.start_date), str(data.end_date)],
        "existing_observations": len(old),
        "backtester_observations": len(new),
        "compared_months": len(overlap),
        "monthly_differences": differences,
        "new_completed_months": [str(d) for d in sorted(new.keys() - old.keys())],
        "common_period_metrics": {"individual_page": old_metrics, "backtester": new_metrics},
        "precision_percent_decimals": data.percent_decimal_places,
        "source_validation": validation.model_dump(mode="json"),
        "snapshot_path": data.snapshot_path,
        "snapshot_sha256": data.snapshot_sha256,
        "existing_observations_sha256": hashlib.sha256(
            "\n".join(f"{d}:{old[d]}" for d in sorted(old)).encode()
        ).hexdigest(),
        "database_observations_modified": False,
        "csv_mechanism": "Client Blob: Period;Return from MAX.rendList (percent units)",
    }


def run_pilot(
    adapter: BacktesterAdapter, repository: ReturnRepository, *, export: bool = False
) -> dict:
    reports = []
    settings = adapter.settings
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    for symbol in ("VTI", "VTV"):
        with repository.factory() as session:
            instrument = session.scalar(
                select(Instrument).where(
                    Instrument.source == repository.source, Instrument.source_symbol == symbol
                )
            )
        if instrument is None:
            raise ReturnExtractionError(f"Pilot instrument {symbol} is absent from catalog")
        data = adapter.extract(symbol, "USD", save_raw=settings.returns_save_raw_snapshots)
        report = compare(repository, data, instrument)
        report["database_comparison_passed"] = report["passed"]
        if not report["passed"] and report["monthly_differences"]:
            # Resolve an older DB snapshot against the *known* current detail URL.
            # Neither the stored series nor its observations are overwritten.
            series = repository.series(instrument.id, "USD")
            url = series.source_metadata["source_url"]
            with adapter._client() as client:
                page = adapter._request(client, "GET", url)
            individual = parse_returns(
                page.text,
                symbol=symbol,
                currency="USD",
                source_url=url,
                extracted_at=data.extracted_at,
            )
            individual_validation = validate_returns(individual)
            values_match = [(o.date, o.return_value) for o in individual.observations] == [
                (o.date, o.return_value) for o in data.observations
            ]
            path = (
                save_snapshot(
                    settings.raw_snapshot_dir / "pilots",
                    page.content,
                    symbol=symbol,
                    currency="USD",
                    method="pilot_individual_page",
                    captured_at=data.extracted_at,
                )
                if settings.returns_save_raw_snapshots
                else None
            )
            confirmed = values_match and individual_validation.passed
            report["source_revision_confirmed"] = confirmed
            report["current_individual_page_comparison"] = {
                "passed": confirmed,
                "snapshot": str(path) if path else None,
                "observation_count": len(individual.observations),
                "monthly_values_identical": values_match,
                "metrics": metrics([o.return_value for o in individual.observations]),
                "backtester_metrics": metrics([o.return_value for o in data.observations]),
                "validation": individual_validation.model_dump(mode="json"),
            }
            report["passed"] = bool(confirmed and report["source_validation"]["passed"])
            report["revision_policy"] = (
                "Existing DB snapshot preserved; revision reported, not applied"
            )
        reports.append(report)
        if export and report["source_validation"]["passed"]:
            directory = settings.report_dir / "pilots"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{symbol}_USD_original.csv").write_text(
                monthly_csv(data), encoding="utf-8"
            )
            export_pilot(data, instrument.id, directory)
        print(
            f"Pilot {symbol}–USD: {'PASS' if report['passed'] else 'FAIL'}; "
            f"{report['compared_months']} months compared; "
            f"{len(report['monthly_differences'])} differences",
            flush=True,
        )
    result = {
        "passed": all(r["passed"] for r in reports),
        "pilots": reports,
        "source_base_url": settings.lazyportfolio_base_url,
    }
    (settings.report_dir / "backtester_pilot_validation.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result
