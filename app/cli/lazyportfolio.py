import argparse
import logging
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.ingestion.base import IngestionError
from app.ingestion.lazyportfolio.models import ParseResult, ValidationReport
from app.ingestion.lazyportfolio.service import LazyPortfolioDiscoveryService
from app.ingestion.lazyportfolio.validation import DiscoveryValidationError
from app.logging_config import configure_logging
from app.repositories.instruments import InstrumentRepository
from app.repositories.returns import ReturnRepository


def print_diagnostics(result: ParseResult, report: ValidationReport) -> None:
    diagnostics = result.diagnostics
    print("\nLazyPortfolioETF instrument discovery\n")
    print(f"Total instruments: {report.instrument_count}")
    print(f"Unique source symbols: {report.unique_symbol_count}")
    print(f"Categories: {len(report.category_counts)}")
    for category, count in report.category_counts.items():
        print(f"  {category}: {count}")
    print("Instrument currencies:")
    for currency, count in result.currency_counts.items():
        print(f"  {currency}: {count}")
    print("Available by simulation currency:")
    for currency, count in result.available_currency_counts.items():
        print(f"  {currency}: {count}")
    print(f"Missing instrument currencies: {len(diagnostics.missing_currencies)}")
    print(f"Missing currency availability: {len(diagnostics.missing_currency_availability)}")
    print(f"Selector copies: {diagnostics.selector_count}")
    print(f"Raw instrument records: {diagnostics.raw_instrument_records}")
    print(f"Identical duplicate records removed: {diagnostics.duplicate_records}")
    print(f"Conflicting symbols: {len(diagnostics.conflicts)}")
    for conflict in diagnostics.conflicts:
        print(f"  Conflict: {conflict.model_dump_json()}")
    print(f"Missing symbols: {diagnostics.missing_symbols}")
    print(f"Empty names: {len(diagnostics.empty_names)}")
    print(f"Unknown/unclassified categories: {len(diagnostics.unclassified_symbols)}")
    print(f"Available-assets panel unique symbols: {diagnostics.panel_symbol_count}")
    print(f"Parser warnings: {len(report.warnings)}")
    for warning in report.warnings:
        print(f"  Warning: {warning}")
    for error in report.errors:
        print(f"  Error: {error}")
    print(f"Validation: {'PASS' if report.passed else 'FAIL'}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QCross instrument discovery")
    # Both `qcross lazyportfolio discover` and `python -m ... discover` are supported.
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["lazyportfolio"]:
        args = args[1:]
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover = subparsers.add_parser("discover")
    discover.add_argument("--save-raw", action=argparse.BooleanOptionalAction, default=None)
    discover.add_argument("--export", action="store_true")
    discover.add_argument("--no-db", action="store_true")
    discover.add_argument("--verbose", action="store_true")
    parsed = parser.parse_args(args)
    engine = None
    try:
        settings = get_settings()
        configure_logging(settings, "discovery", verbose=parsed.verbose)
        repository = None
        return_status_provider = None
        if not parsed.no_db:
            engine = make_engine(settings.database_url)
            factory = make_session_factory(engine)
            repository = InstrumentRepository(factory)
            return_status_provider = ReturnRepository(factory, source="lazyportfolioetf")
        print("Fetching LazyPortfolioETF asset universe...", flush=True)
        run = LazyPortfolioDiscoveryService(
            settings, repository, return_status_provider=return_status_provider
        ).discover(
            save_raw=parsed.save_raw,
            export=parsed.export,
        )
        print_diagnostics(run.parsed, run.validation)
        if run.sync:
            print(f"Database inserted: {run.sync.inserted}")
            print(f"Database updated: {run.sync.updated}")
            print(f"Database unchanged (seen timestamps refreshed): {run.sync.unchanged}")
            print(f"Database deactivated: {run.sync.deactivated}")
        else:
            print("Database synchronization skipped; no stored historical baseline checked")
        if run.snapshot_path:
            print(f"Raw snapshot: {run.snapshot_path}")
        for path in run.export_paths:
            print(f"Export written: {path}")
        return 0
    except DiscoveryValidationError as exc:
        print_diagnostics(exc.result, exc.report)
        return 1
    except (IngestionError, SQLAlchemyError, OSError, ValueError) as exc:
        logging.getLogger(__name__).error("Discovery failed: %s", exc)
        if isinstance(exc, SQLAlchemyError):
            print(
                "Check DATABASE_URL and run `alembic upgrade head` before discovery.",
                file=sys.stderr,
            )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
