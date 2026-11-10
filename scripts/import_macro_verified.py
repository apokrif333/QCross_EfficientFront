"""Import verified BIL HTML locally and prove nominal observations remain unchanged."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.ingestion.lazyportfolio.inflation import extract_inflation
from app.ingestion.lazyportfolio.macro_service import refresh


def nominal_fingerprint(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(
            "select series_id,date,return_value from monthly_returns order by series_id,date"
        ):
            digest.update(json.dumps(row, separators=(",", ":")).encode())
            count += 1
        series = connection.execute("select count(*) from return_series").fetchone()[0]
    return {"series": series, "observations": count, "sha256": digest.hexdigest()}


def main():
    settings = get_settings()
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError(
            "This local verification helper is SQLite-only; use macro CLI for PostgreSQL."
        )
    database = settings.database_url.removeprefix("sqlite:///")
    before = nominal_fingerprint(database)
    command.upgrade(Config("alembic.ini"), "head")
    html = Path("tests/fixtures/lazyportfolio_bil_inflation.html").read_text(encoding="utf-8")
    rows, nominal, snapshot = extract_inflation(
        html,
        source_url="https://www.lazyportfolioetf.com/etf/spdr-blmbg-barclays-1-3-mth-t-bill-bil/",
        extracted_at=datetime.now(UTC),
    )
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            report = refresh(session, rows, nominal, snapshot)
            if report["pending_revisions"]:
                raise RuntimeError(
                    "Pending inflation revisions require explicit acceptance; "
                    "transaction rolled back."
                )
            session.commit()
    finally:
        engine.dispose()
    after = nominal_fingerprint(database)
    assert before == after, "Nominal source observations changed"
    result = {
        "import": report,
        "nominal_before": before,
        "nominal_after": after,
        "scope": "local SQLite only; not a Railway deployment",
    }
    Path("docs/macro-import-verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
