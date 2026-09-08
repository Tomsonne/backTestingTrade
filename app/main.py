from __future__ import annotations

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.responses import FileResponse

from .api import build_api_router
from .config import Settings
from .jobs import APP_VERSION, execute
from .research.presets import builtin_presets
from .storage import init_db, seed_presets


LOGGER = logging.getLogger(__name__)
STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


def create_app(config: Settings | None = None) -> FastAPI:
    settings = config or Settings()
    settings.ensure_dirs()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="riseup-research")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        init_db(settings.db_path)
        seed_presets(settings.db_path, builtin_presets())
        if settings.enable_scheduler:
            scheduler = BackgroundScheduler(timezone=settings.timezone)
            scheduler.add_job(
                lambda: execute(settings),
                CronTrigger(
                    day_of_week="mon-fri", hour="*", minute=settings.schedule_minute,
                    timezone=settings.timezone,
                ),
                id="weekday_backtest", replace_existing=True, max_instances=1, coalesce=True,
            )
            scheduler.start()
            application.state.scheduler = scheduler
        provider_ready = settings.data_provider == "dukascopy" or bool(settings.twelve_data_api_key)
        if settings.auto_run_on_startup and provider_ready:
            executor.submit(execute, settings)
        yield
        scheduler = application.state.scheduler
        if scheduler:
            scheduler.shutdown(wait=False)
        executor.shutdown(wait=False, cancel_futures=False)

    application = FastAPI(
        title="RiseUp Trading Research Lab",
        version=APP_VERSION,
        description="Local, research-only Forex strategy laboratory.",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.executor = executor
    application.state.scheduler = None
    application.include_router(build_api_router(settings, executor))

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_INDEX)

    return application


app = create_app()
