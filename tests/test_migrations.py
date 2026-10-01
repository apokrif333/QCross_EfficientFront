from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from app.config import get_settings
from app.db.session import make_engine


def test_migration_upgrade_schema_and_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        engine = make_engine(database_url)
        try:
            tables = inspect(engine).get_table_names()
            assert {
                "instruments",
                "ingestion_states",
                "return_series",
                "monthly_returns",
                "return_revisions",
                "return_imports",
            } <= set(tables)
            constraints = inspect(engine).get_unique_constraints("instruments")
            assert any(c["column_names"] == ["source", "source_symbol"] for c in constraints)
            constraints = inspect(engine).get_unique_constraints("monthly_returns")
            assert any(c["column_names"] == ["series_id", "date"] for c in constraints)
        finally:
            engine.dispose()
        command.check(config)
        command.downgrade(config, "base")
        engine = make_engine(database_url)
        try:
            assert "instruments" not in inspect(engine).get_table_names()
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
