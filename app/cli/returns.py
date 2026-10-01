import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.models import Instrument, MonthlyReturn
from app.db.session import make_engine, make_session_factory
from app.exports.returns import export_returns
from app.ingestion.base import IngestionError
from app.ingestion.lazyportfolio.backtester_pilot import run_pilot
from app.ingestion.lazyportfolio.return_pairs import ReturnPairService
from app.ingestion.lazyportfolio.returns_parser import _parse_returns
from app.ingestion.lazyportfolio.returns_service import LazyPortfolioReturnsService
from app.ingestion.lazyportfolio.returns_validation import validate_returns
from app.logging_config import configure_logging
from app.repositories.returns import ReturnRepository
from app.storage.snapshots import read_snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QCross historical monthly returns")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("discover", "update"):
        command = commands.add_parser(name)
        group = command.add_mutually_exclusive_group(required=True)
        group.add_argument("--symbol")
        group.add_argument("--all", action="store_true", dest="all_active")
        command.add_argument("--save-raw", action=argparse.BooleanOptionalAction, default=None)
        command.add_argument("--accept-revisions", action="store_true")
        command.add_argument("--currency")
        command.add_argument("--verbose", action="store_true")
    command = commands.add_parser("export")
    command.add_argument("--format", choices=["csv", "parquet"], default="parquet")
    command.add_argument("--symbol")
    command.add_argument("--currency")
    command.add_argument("--wide", action="store_true")
    command.add_argument("--output", type=Path)
    command = commands.add_parser("validate")
    command.add_argument("--symbol", required=True)
    command.add_argument("--currency")
    command.add_argument(
        "--live",
        action="store_true",
        help="Check live source without database changes; default checks stored snapshot",
    )
    commands.add_parser("coverage")
    commands.add_parser("backtester-pilot", help="Read-only VTI/VTV cross-validation")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    settings = get_settings()
    configure_logging(settings, "returns", verbose=getattr(args, "verbose", False))
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    repository = ReturnRepository(factory, source="lazyportfolioetf")
    service = LazyPortfolioReturnsService(settings, repository)
    pair_service = ReturnPairService(settings, repository, individual=service)
    try:
        if args.command == "backtester-pilot":
            report = run_pilot(pair_service.adapter, repository, export=True)
            print(json.dumps(report, indent=2))
            return int(not report["passed"])
        if args.command in {"discover", "update"}:
            report = pair_service.run(
                symbol=args.symbol,
                all_active=args.all_active,
                update=args.command == "update",
                accept_revisions=args.accept_revisions,
                save_raw=args.save_raw,
                currency=args.currency,
            )
            print(
                json.dumps(
                    {"coverage": report["status_counts"], "batch_stopped": report["batch_stopped"]},
                    indent=2,
                )
            )
            selected = set(map(tuple, report["selected_pairs"]))
            return int(
                report["batch_stopped"] is not None
                or any(
                    r["status"] != "completed"
                    for r in report["pairs"]
                    if (r["ticker"], r["currency"]) in selected
                    and (r["eligible"] or not args.all_active)
                )
            )
        if args.command == "export":
            if args.symbol and not args.currency:
                with factory() as session:
                    instrument = session.scalar(
                        select(Instrument).where(
                            Instrument.source == repository.source,
                            Instrument.source_symbol == args.symbol,
                        )
                    )
                if instrument is None or not instrument.currency:
                    raise ValueError(
                        "Export requires a known native currency or explicit --currency"
                    )
                args.currency = instrument.currency
            suffix = "_wide" if args.wide else ""
            label = f"{args.symbol}_{args.currency or 'native'}" if args.symbol else "lazyportfolio"
            path = (
                args.output
                or (settings.export_dir / "pairs" if args.symbol else settings.export_dir)
                / f"{label}_monthly_returns{suffix}.{args.format}"
            )
            print(
                export_returns(
                    factory,
                    path,
                    format=args.format,
                    symbol=args.symbol,
                    currency=args.currency,
                    wide=args.wide,
                )
            )
            return 0
        if args.command == "coverage":
            print(json.dumps(pair_service.coverage()["status_counts"], indent=2))
            return 0
        with factory() as session:
            instrument = session.scalar(
                select(Instrument).where(
                    Instrument.source == "lazyportfolioetf", Instrument.source_symbol == args.symbol
                )
            )
        if instrument is None or instrument.currency is None:
            raise ValueError("No catalog instrument with a known native currency")
        currency = args.currency.upper() if args.currency else instrument.currency
        series = repository.series(instrument.id, currency)
        if args.live:
            urls = {
                "VTI": settings.lazyportfolio_base_url.rstrip("/")
                + "/etf/vanguard-total-stock-market-vti/"
            }
            if args.symbol not in urls or currency != instrument.currency:
                urls = service.public_page_index()
            if (
                args.symbol not in urls
                or currency != instrument.currency
                or (series is not None and series.extraction_method == "backtester")
            ):
                data = pair_service.adapter.extract(args.symbol, currency)
                report = validate_returns(data)
                print(report.model_dump_json(indent=2))
                return int(not report.passed)
            page = service.fetch(urls[args.symbol])
            html, url, extracted_at = page.html, page.url, page.fetched_at
        else:
            if series is None or not series.source_metadata.get("snapshot_path"):
                raise ValueError(
                    "Stored validation requires a raw snapshot; alternatively use --live"
                )
            raw = read_snapshot(series.source_metadata["snapshot_path"], settings.archive_dir)
            expected_hash = series.source_metadata.get("snapshot_sha256")
            if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
                raise ValueError("Raw snapshot hash differs from the committed extraction")
            html = raw.decode(series.source_metadata.get("snapshot_encoding", "utf-8-sig"))
            url = series.source_metadata["source_url"]
            extracted_at = series.last_updated_at
            if extracted_at.tzinfo is None:
                extracted_at = extracted_at.replace(tzinfo=UTC)
        data = _parse_returns(
            html,
            symbol=args.symbol,
            currency=currency,
            source_url=url,
            extracted_at=extracted_at,
            backtester=bool(series and series.extraction_method == "backtester" and not args.live),
            component_symbol=series.source_metadata.get("source_component_symbol")
            if series and not args.live
            else None,
        )
        report = validate_returns(data)
        if not args.live and series:
            with factory() as session:
                stored = list(
                    session.scalars(
                        select(MonthlyReturn)
                        .where(MonthlyReturn.series_id == series.id)
                        .order_by(MonthlyReturn.date)
                    )
                )
            pairs = [(o.date, o.return_value) for o in data.observations]
            if [(o.date, o.return_value) for o in stored] != pairs:
                report.errors.append(
                    "Stored observations differ from validated raw source snapshot"
                )
                report.passed = False
        print(report.model_dump_json(indent=2))
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        (settings.report_dir / "latest_validation.json").write_text(
            json.dumps(
                {
                    "ticker": args.symbol,
                    "currency": currency,
                    "scope": "live_source" if args.live else "stored_source_snapshot",
                    **report.model_dump(mode="json"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return int(not report.passed)
    except KeyboardInterrupt:
        print("Interrupted. Completed instruments are committed; rerun discover to resume.")
        return 130
    except (IngestionError, SQLAlchemyError, ValueError, OSError) as exc:
        logging.getLogger(__name__).error("Returns command failed: %s", exc)
        return 1
    finally:
        pair_service.adapter.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
