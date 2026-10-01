"""QCross backend application."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from app.api.router import router
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    application_engine = engine or make_engine(settings.database_url)
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is None:
            application_engine.dispose()

    application = FastAPI(title="QCross Instrument API", version="0.1.0", lifespan=lifespan)
    application.state.session_factory = make_session_factory(application_engine)
    application.include_router(router)
    return application


app = create_app()
