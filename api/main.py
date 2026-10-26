import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from api.routers import admin as admin_router
from api.routers import auth as auth_router
from api.routers import health as health_router
from api.routers import sessions as sessions_router
from api.routers import tests as tests_router
from core.db import async_session_factory
from core.exceptions import register_exception_handlers
from core.logging import RequestIdMiddleware, configure_logging
from core.services.sessions import run_auto_submit_loop, run_startup_scoring
from core.settings import get_settings

OPENAPI_TAGS = [
    {"name": "auth", "description": "Registration, login, token lifecycle."},
    {"name": "health", "description": "Liveness and readiness checks."},
    {"name": "admin", "description": "Admin-only: users, results, grading oversight."},
    {"name": "tests", "description": "Published tests students can take."},
    {"name": "sessions", "description": "Taking a test: start, autosave answers, submit."},
]


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    tasks = []
    if settings.auto_submit_enabled:
        tasks.append(
            asyncio.create_task(
                run_auto_submit_loop(async_session_factory, settings.auto_submit_interval_seconds)
            )
        )
    if settings.startup_scoring_enabled:
        # Runs in the background: startup and health checks never wait for it.
        tasks.append(asyncio.create_task(run_startup_scoring(async_session_factory)))
    yield
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="IELTS Backend", version="0.1.0", openapi_tags=OPENAPI_TAGS, lifespan=_lifespan
    )
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router.router)
    app.include_router(auth_router.router)
    app.include_router(admin_router.router)
    app.include_router(tests_router.router)
    app.include_router(sessions_router.router)
    return app


app = create_app()
