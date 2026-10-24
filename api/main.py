from fastapi import FastAPI

from api.routers import admin as admin_router
from api.routers import auth as auth_router
from api.routers import health as health_router
from api.routers import sessions as sessions_router
from api.routers import tests as tests_router
from core.exceptions import register_exception_handlers
from core.logging import RequestIdMiddleware, configure_logging

OPENAPI_TAGS = [
    {"name": "auth", "description": "Registration, login, token lifecycle."},
    {"name": "health", "description": "Liveness and readiness checks."},
    {"name": "admin", "description": "Admin-only: users, results, grading oversight."},
    {"name": "tests", "description": "Published tests students can take."},
    {"name": "sessions", "description": "Taking a test: start, autosave answers, submit."},
]


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="IELTS Backend", version="0.1.0", openapi_tags=OPENAPI_TAGS)
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router.router)
    app.include_router(auth_router.router)
    app.include_router(admin_router.router)
    app.include_router(tests_router.router)
    app.include_router(sessions_router.router)
    return app


app = create_app()
