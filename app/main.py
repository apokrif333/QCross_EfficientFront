"""QCross backend application."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from sqlalchemy import Engine

from app.analytics.jobs import AnalyticsJobs
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
        monitor = asyncio.create_task(_app.state.analytics_jobs.monitor())
        try:
            yield
        finally:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
            _app.state.analytics_jobs.close()
            if engine is None:
                application_engine.dispose()

    application = FastAPI(title="QCross Instrument API", version="0.1.0", lifespan=lifespan)
    application.state.session_factory = make_session_factory(application_engine)
    application.state.analytics_jobs = AnalyticsJobs(
        workers=settings.analytics_workers,
        timeout_seconds=settings.analytics_timeout_seconds,
        max_jobs=settings.analytics_max_jobs,
        retention_seconds=settings.analytics_retention_seconds,
    )
    application.include_router(router)
    return application


app = create_app()
