from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.repositories.instruments import InstrumentRepository


@pytest.fixture
def source_html() -> str:
    return Path("tests/fixtures/lazyportfolio_asset_universe.html").read_text(encoding="utf-8")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        export_dir=tmp_path / "exports",
        raw_snapshot_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        archive_dir=tmp_path / "archive",
    )


@pytest.fixture
def session_factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def repository(session_factory: sessionmaker[Session]) -> InstrumentRepository:
    return InstrumentRepository(session_factory)
