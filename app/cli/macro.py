"""Refresh only BIL/inflation; no per-asset downloads or nominal-return writes."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Instrument
from app.db.models.reference import ReferenceSeries
from app.db.session import make_engine, make_session_factory
from app.ingestion.lazyportfolio.client import LazyPortfolioClient
from app.ingestion.lazyportfolio.inflation import extract_inflation
from app.ingestion.lazyportfolio.macro_service import bil_series, refresh


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "preview-bil-inflation",
            "import-bil-inflation",
            "validate-bil-inflation",
            "status",
        ],
    )
    parser.add_argument(
        "--html", type=Path, help="Original saved BIL HTML snapshot (offline/cache)"
    )
    parser.add_argument("--accept-revisions", action="store_true")
    parser.add_argument(
        "--published-month",
        action="append",
        default=[],
        help="Explicitly resolve a maintained publication exception",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            if args.command == "status":
                print(
                    json.dumps(
                        [
                            {
                                "code": r.series_code,
                                "count": r.observation_count,
                                "start": str(r.start_date),
                                "end": str(r.end_date),
                                "version": r.data_version,
                                "metadata": r.source_metadata,
                            }
                            for r in session.scalars(select(ReferenceSeries))
                        ],
                        indent=2,
                    )
                )
                return 0
            bil = bil_series(session)
            instrument = session.get(Instrument, bil.instrument_id)
            url = bil.source_metadata.get("source_url") or instrument.source_url
            if args.html:
                html = args.html.read_text(encoding="utf-8")
            else:
                page = LazyPortfolioClient(
                    settings.model_copy(
                        update={
                            "http_backoff_seconds": max(
                                settings.http_backoff_seconds, settings.returns_request_interval
                            )
                        }
                    )
                ).fetch_page(url)
                html = page.html
                snapshot = hashlib.sha256(html.encode()).hexdigest()
                directory = settings.raw_snapshot_dir / "macro"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f"{snapshot}.html").write_text(html, encoding="utf-8")
            rows, nominal, snapshot = extract_inflation(
                html,
                source_url=url,
                extracted_at=datetime.now(UTC),
                published_months=tuple(args.published_month),
            )
            report = refresh(
                session, rows, nominal, snapshot, accept_revisions=args.accept_revisions
            )
            if args.command == "import-bil-inflation":
                session.commit()
            else:
                session.rollback()
            print(json.dumps(report, indent=2))
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
