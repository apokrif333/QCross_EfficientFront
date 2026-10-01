from pathlib import Path
from sqlite3 import Connection
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.pool.base import ConnectionPoolEntry


def make_engine(database_url: str) -> Engine:
    # Railway commonly supplies postgres://; explicitly choose psycopg v3.
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if not url.database or url.database == ":memory:":
            kwargs["poolclass"] = StaticPool
        else:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def configure_sqlite(connection: Connection, _record: ConnectionPoolEntry) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
