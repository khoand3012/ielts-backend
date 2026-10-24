# IELTS Backend — Foundation Phase Implementation Plan

> **Historical record — superseded in part.** Built as written on 2026-06-24, then changed: the ARQ worker, Redis and the Redis health check (Task 14 and parts of Tasks 2, 12, 15) were removed on 2026-10-20 for a single-process monolith, and the AI provider moved to OpenRouter (`OPENROUTER_API_KEY`, `AI_MODEL`). For the current design see `specs/2026-06-23-backend-foundation-design.md` and `CLAUDE.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the bootable, deployable, CI-green Foundation of the IELTS FastAPI backend — settings, async DB, models, Alembic, structured errors/logging, full JWT auth flow, a worker that boots against Redis, and local/CI parity — so every later domain phase (test content, grading, AI, entitlements, media) plugs into a working skeleton.

**Architecture:** Modular monolith plus a separate ARQ worker, both importing a shared `core/` package. The API is a stateless FastAPI app (auth + health this phase); the worker is an always-on ARQ process proving the second deployable boots. All state lives in Postgres (async SQLAlchemy 2.0 + asyncpg) and Redis. One Docker image runs both processes; Docker Compose provides Postgres + Redis + MinIO locally.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (async, asyncpg), Pydantic v2 + pydantic-settings, Alembic, ARQ, Redis, `argon2-cffi`, `python-jose`, `uuid-utils`, `structlog`, `ruff`, `mypy` (strict), `pytest` + `pytest-asyncio` + `httpx` + `testcontainers`, managed by `uv`.

## Global Constraints

- **Python**: 3.11+ (`requires-python = ">=3.11"`).
- **Async everywhere**: no sync DB calls; HTTP via `httpx.AsyncClient` only — never `requests`.
- **Settings**: only `core/settings.py` may read env vars. Nothing else touches `os.environ`.
- **Time**: all timestamps are timezone-aware UTC; DB columns are `TIMESTAMPTZ`. Never store naive datetimes. Use `datetime.now(timezone.utc)`.
- **IDs**: UUIDv7 primary keys (sortable), generated in Python via `uuid_utils.compat.uuid7()` (returns a stdlib `uuid.UUID`). Never auto-increment integers.
- **Schemas vs models**: never return SQLAlchemy ORM objects from endpoints — always Pydantic schemas.
- **Service layer**: routers are thin (parse → call service → return schema). Business logic lives in `core/services/`.
- **Errors**: services raise typed `AppError` subclasses; a single exception handler maps them to HTTP. Services NEVER raise `HTTPException`.
- **Logging**: `structlog` only. No `print()` or `logger.info()` debugging left in committed code.
- **Migrations**: schema changes run through Alembic only. Never `Base.metadata.create_all()` outside tests. Never auto-migrate on boot.
- **JWT lifetimes**: access 15 min, refresh 30 day. Refresh tokens rotate on use (revoke-on-use, persisted by `jti`).
- **OpenAPI contract**: every endpoint has an explicit `tags=[...]` and a stable `operation_id`. Naming convention: `operation_id` = `"{tag}_{action}"` in snake_case (e.g. `auth_register`, `auth_login`, `health_check`).
- **Lint/format/type gate** (must pass before any commit that touches code): `ruff check .` && `ruff format --check .` && `mypy .`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | uv deps, ruff + mypy (strict) config, pytest config |
| `core/settings.py` | one `Settings` (pydantic-settings); all env config |
| `core/db.py` | async engine (asyncpg), `async_session_factory`, `Base` |
| `core/logging.py` | structlog config + `RequestIdMiddleware` + `get_logger` |
| `core/exceptions.py` | `AppError` hierarchy + `register_exception_handlers(app)` |
| `core/models/base.py` | `Base`, `UUIDPrimaryKeyMixin`, `TimestampMixin` |
| `core/models/user.py` | `User` model (`plan` enum, `password_hash`) |
| `core/models/refresh_token.py` | `RefreshToken` model (`jti`, `revoked_at`) |
| `core/schemas/auth.py` | register/login/refresh request + token response DTOs |
| `core/schemas/user.py` | `UserRead` response DTO |
| `core/services/security.py` | argon2 hashing + JWT encode/decode helpers |
| `core/services/auth.py` | register/login/refresh/logout/me business logic |
| `api/deps.py` | `get_db`, `get_current_user`, `get_settings` |
| `api/routers/health.py` | `GET /health` (DB + Redis ping) |
| `api/routers/auth.py` | auth endpoints (thin) |
| `api/main.py` | app factory, router include, exc handlers, OpenAPI tags |
| `worker/main.py` | ARQ `WorkerSettings` (Redis connection) |
| `worker/tasks/__init__.py` | `healthcheck` task |
| `alembic/env.py` | async migration environment |
| `alembic/versions/0001_*.py` | first migration: `users` + `refresh_tokens` |
| `docker-compose.yml` | postgres + redis + minio |
| `Dockerfile` | one image; `api` and `worker` process entrypoints |
| `.github/workflows/ci.yml` | uv sync → ruff → mypy → pytest |
| `tests/conftest.py` | testcontainers Postgres + httpx app client fixtures |
| `tests/test_*.py` | per-task tests |

---

## Task 1: Project scaffolding & tooling

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.python-version`
- Create: `core/__init__.py`, `api/__init__.py`, `worker/__init__.py`, `tests/__init__.py`
- Create: `.env.example`
- Create: `tests/conftest.py` (env bootstrap only; fixtures added in Tasks 7 & 12)

**Interfaces:**
- Consumes: nothing.
- Produces: a `uv`-managed project named `ielts-backend`; importable empty packages `core`, `api`, `worker`; ruff + mypy + pytest configured; a `tests/conftest.py` that sets required env vars **before any test imports a `core.*` module** (several modules call `get_settings()` at import time, so the env must exist at collection time).

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "ielts-backend"
version = "0.1.0"
description = "IELTS practice platform backend"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.10",
    "pydantic-settings>=2.7",
    "arq>=0.26",
    "redis>=5.2",
    "argon2-cffi>=23.1",
    "python-jose[cryptography]>=3.3",
    "uuid-utils>=0.10",
    "structlog>=24.4",
    "httpx>=0.28",
    "aioboto3>=13.2",
]

[dependency-groups]
dev = [
    "ruff>=0.8",
    "mypy>=1.14",
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "testcontainers[postgres]>=4.9",
    "asgi-lifespan>=2.1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["core", "api", "worker"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC", "RUF"]

[tool.mypy]
python_version = "3.11"
strict = true
plugins = ["pydantic.mypy"]
warn_unused_ignores = true
disallow_untyped_defs = true

[[tool.mypy.overrides]]
module = ["jose.*", "uuid_utils.*", "testcontainers.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.python-version`, `.gitignore`, package `__init__.py` files, and `.env.example`**

`.python-version`:
```
3.11
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.env
.mypy_cache/
.pytest_cache/
.ruff_cache/
*.egg-info/
dist/
```

Create empty files: `core/__init__.py`, `api/__init__.py`, `worker/__init__.py`, `worker/tasks/__init__.py` (placeholder, replaced in Task 14), `tests/__init__.py`, `api/routers/__init__.py`, `core/models/__init__.py`, `core/schemas/__init__.py`, `core/services/__init__.py`.

`.env.example`:
```
DATABASE_URL=postgresql+asyncpg://ielts:ielts@localhost:5432/ielts
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-me-in-prod-use-a-long-random-string
R2_ENDPOINT_URL=http://localhost:9000
R2_ACCESS_KEY_ID=minioadmin
R2_SECRET_ACCESS_KEY=minioadmin
R2_BUCKET=ielts-assets
OPENROUTER_API_KEY=sk-or-xxx
```

- [ ] **Step 3: Create `tests/conftest.py` with the env bootstrap**

Several `core.*` modules (`core/db.py`, `core/services/security.py`) call `get_settings()` at **import time**, and `Settings` fields are required with no defaults. Without this block, pytest fails with a `ValidationError` at *collection* time — before any fixture runs. This file gets extended (not recreated) in Tasks 7 and 12.

```python
# tests/conftest.py
import os

# Set required env BEFORE any test imports a core.* module that calls
# get_settings() at import time. Placeholder values are fine: the real DB tests
# use the testcontainers _engine fixture / get_db override (Tasks 7, 12), not
# the lazily-created core.db.engine.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost:5432/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("R2_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("R2_ACCESS_KEY_ID", "x")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "x")
os.environ.setdefault("R2_BUCKET", "x")
os.environ.setdefault("OPENROUTER_API_KEY", "x")
```

- [ ] **Step 4: Sync deps and verify tooling runs on the empty project**

Run: `uv sync`
Expected: resolves and installs without error; creates `.venv` and `uv.lock`.

- [ ] **Step 5: Verify lint + type check pass on the skeleton**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy .`
Expected: ruff reports "All checks passed!"; mypy reports "Success: no issues found" (empty packages have nothing to flag). If `ruff format --check` fails, run `uv run ruff format .` first, then re-check.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore .env.example core api worker tests
git commit -m "chore: scaffold uv project with ruff, mypy, pytest"
```

---

## Task 2: Settings

**Files:**
- Create: `core/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` (BaseSettings) with attributes `database_url: str`, `redis_url: str`, `jwt_secret: str`, `jwt_algorithm: str = "HS256"`, `access_token_ttl_seconds: int = 900`, `refresh_token_ttl_seconds: int = 2592000`, `r2_endpoint_url: str`, `r2_access_key_id: str`, `r2_secret_access_key: str`, `r2_bucket: str`, `openrouter_api_key: str`, `free_writing_daily: int = 3`, `free_speaking_daily: int = 3`, `paid_writing_daily: int = 30`, `paid_speaking_daily: int = 30`, `env: str = "local"`. Module-level `get_settings() -> Settings` (cached singleton).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings.py
import os
from core.settings import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "bucket")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    s = Settings()
    assert s.database_url.endswith("/d")
    assert s.access_token_ttl_seconds == 900
    assert s.free_writing_daily == 3
    assert s.paid_speaking_daily == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.settings'`.

- [ ] **Step 3: Write the implementation**

```python
# core/settings.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"

    database_url: str
    redis_url: str

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000

    r2_endpoint_url: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str

    openrouter_api_key: str

    free_writing_daily: int = 3
    free_speaking_daily: int = 3
    paid_writing_daily: int = 30
    paid_speaking_daily: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add core/settings.py tests/test_settings.py
git commit -m "feat: typed settings via pydantic-settings"
```

---

## Task 3: Async database engine, session factory, Base

**Files:**
- Create: `core/db.py`
- Create: `core/models/base.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces:
  - `core/models/base.py`: `Base` (DeclarativeBase with naming convention), `UUIDPrimaryKeyMixin` (provides `id: Mapped[uuid.UUID]` defaulting to `uuid7()`), `TimestampMixin` (provides `created_at`, `updated_at` as tz-aware).
  - `core/db.py`: `engine` (AsyncEngine), `async_session_factory` (async_sessionmaker), `get_session() -> AsyncIterator[AsyncSession]` async context-manager helper.

- [ ] **Step 1: Write `core/models/base.py`**

```python
# core/models/base.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_utils.compat import uuid7

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
```

- [ ] **Step 2: Write `core/db.py`**

```python
# core/db.py
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.settings import get_settings

settings = get_settings()

# statement_cache_size=0 is required for PgBouncer transaction mode (Neon pooled
# endpoint), which does not support asyncpg prepared statements.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"statement_cache_size": 0},
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_db.py
from sqlalchemy import text


async def test_engine_executes_select(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1
```

This test depends on the `db_session` fixture created in Task 7 (`tests/conftest.py`). If running Task 3 before Task 7, instead verify imports only with the temporary test below, then delete it when conftest lands:

```python
# tests/test_db.py (temporary import smoke test)
def test_db_module_imports():
    from core.db import async_session_factory, engine, get_session  # noqa: F401
    from core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin  # noqa: F401
```

- [ ] **Step 4: Run the smoke test**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (import smoke test). The `SELECT 1` version is run after Task 7 provides a live Postgres fixture.

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add core/db.py core/models/base.py tests/test_db.py
git commit -m "feat: async engine, session factory, declarative base"
```

---

## Task 4: Structured logging + request-id middleware

**Files:**
- Create: `core/logging.py`
- Test: `tests/test_logging.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `configure_logging() -> None` — sets up structlog processors.
  - `get_logger(name: str) -> structlog.stdlib.BoundLogger`.
  - `RequestIdMiddleware` — Starlette/ASGI middleware (`BaseHTTPMiddleware` subclass) that reads/creates an `X-Request-ID` header, binds it into structlog contextvars for the request, and echoes it on the response.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logging.py
from core.logging import configure_logging, get_logger


def test_get_logger_returns_bound_logger():
    configure_logging()
    log = get_logger("test")
    log.info("hello", key="value")  # must not raise
    assert log is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.logging'`.

- [ ] **Step 3: Write the implementation**

```python
# core/logging.py
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_logging.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add core/logging.py tests/test_logging.py
git commit -m "feat: structlog config + request-id middleware"
```

---

## Task 5: Exception hierarchy + HTTP mapping

**Files:**
- Create: `core/exceptions.py`
- Test: `tests/test_exceptions.py`

**Interfaces:**
- Consumes: nothing (FastAPI app passed in at registration).
- Produces:
  - `AppError(Exception)` base with `status_code: int = 500`, `code: str = "internal_error"`, `message: str`, optional `extra: dict[str, object]`.
  - Subclasses: `NotFoundError` (404, `"not_found"`), `ConflictError` (409, `"conflict"`), `AuthError` (401, `"unauthorized"`), `ForbiddenError` (403, `"forbidden"`), `ValidationAppError` (422, `"validation_error"`). (`QuotaExceededError` is added in the Entitlements phase, not here.)
  - `register_exception_handlers(app: FastAPI) -> None` — registers a handler that returns JSON `{"error": {"code", "message", "extra"}}` with the error's status code.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exceptions.py
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.exceptions import AuthError, NotFoundError, register_exception_handlers


def test_app_error_attributes():
    err = NotFoundError("user missing")
    assert err.status_code == 404
    assert err.code == "not_found"
    assert err.message == "user missing"


async def test_handler_maps_error_to_json():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise AuthError("nope")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom")
    assert resp.status_code == 401
    assert resp.json() == {"error": {"code": "unauthorized", "message": "nope", "extra": {}}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_exceptions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.exceptions'`.

- [ ] **Step 3: Write the implementation**

```python
# core/exceptions.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, extra: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, "extra": exc.extra}
            },
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_exceptions.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add core/exceptions.py tests/test_exceptions.py
git commit -m "feat: AppError hierarchy + HTTP exception handler"
```

---

## Task 6: User & RefreshToken models

**Files:**
- Create: `core/models/user.py`
- Create: `core/models/refresh_token.py`
- Modify: `core/models/__init__.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `Base`, `UUIDPrimaryKeyMixin`, `TimestampMixin` from Task 3.
- Produces:
  - `User` (`__tablename__ = "users"`): `id`, `email: Mapped[str]` (unique, indexed), `password_hash: Mapped[str]`, `plan: Mapped[str]` (default `"free"`), `created_at`, `updated_at`.
  - `RefreshToken` (`__tablename__ = "refresh_tokens"`): `id`, `user_id: Mapped[uuid.UUID]` (FK → `users.id`, indexed), `jti: Mapped[str]` (unique, indexed), `expires_at: Mapped[datetime]` (tz-aware), `revoked_at: Mapped[datetime | None]`, `created_at`, `updated_at`.
  - `core/models/__init__.py` re-exports `Base`, `User`, `RefreshToken` so Alembic autogenerate sees all tables.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
import uuid

from core.models import RefreshToken, User


def test_user_defaults():
    user = User(email="a@b.com", password_hash="x")
    assert user.plan == "free" or user.plan is None  # default applied at flush


def test_refresh_token_fields():
    rt = RefreshToken(user_id=uuid.uuid4(), jti="abc", expires_at=None)
    assert rt.jti == "abc"
    assert rt.revoked_at is None
```

Note: SQLAlchemy column `default=` is applied at INSERT, so a freshly constructed (un-flushed) `User.plan` may be `None`; the assertion above tolerates both. The real default is verified end-to-end in the auth tests (Task 17).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'User' from 'core.models'`.

- [ ] **Step 3: Write the models**

```python
# core/models/user.py
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    plan: Mapped[str] = mapped_column(String(16), default="free", server_default="free")
```

```python
# core/models/refresh_token.py
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
```

```python
# core/models/__init__.py
from core.models.base import Base
from core.models.refresh_token import RefreshToken
from core.models.user import User

__all__ = ["Base", "RefreshToken", "User"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add core/models tests/test_models.py
git commit -m "feat: User and RefreshToken models"
```

---

## Task 7: Alembic async setup, first migration, and test fixtures

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial.py`
- Modify: `tests/conftest.py` (add DB fixtures; keep the Task 1 env block)
- Modify: `tests/test_db.py` (swap smoke test for the live `SELECT 1` test from Task 3)

**Interfaces:**
- Consumes: `Base` and all models from Task 6; `get_settings()` from Task 2.
- Produces:
  - A working `alembic upgrade head` that creates `users` + `refresh_tokens`.
  - `tests/conftest.py` fixtures: `postgres_url` (session-scoped, spins a testcontainers Postgres and runs `Base.metadata.create_all`), `db_session` (function-scoped `AsyncSession`), `app_client` (function-scoped `httpx.AsyncClient` bound to the FastAPI app — added/used from Task 12 onward).

- [ ] **Step 1: Create `alembic.ini`**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 2: Create `alembic/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 3: Create `alembic/env.py` (async)**

```python
# alembic/env.py
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from core.models import Base  # noqa: F401  (imports all models via package)
from core.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(
        get_settings().database_url, connect_args={"statement_cache_size": 0}
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 4: Generate the first migration against a scratch Postgres**

Start a throwaway Postgres (Docker Compose lands in Task 15; for now use a one-off container):

Run:
```bash
docker run -d --name ielts-pg-tmp -e POSTGRES_USER=ielts -e POSTGRES_PASSWORD=ielts -e POSTGRES_DB=ielts -p 5432:5432 postgres:15
DATABASE_URL=postgresql+asyncpg://ielts:ielts@localhost:5432/ielts \
  REDIS_URL=redis://localhost:6379/0 JWT_SECRET=x \
  R2_ENDPOINT_URL=x R2_ACCESS_KEY_ID=x R2_SECRET_ACCESS_KEY=x R2_BUCKET=x \
  OPENROUTER_API_KEY=x \
  uv run alembic revision --autogenerate -m "initial users and refresh tokens"
```
Expected: a file `alembic/versions/<hash>_initial_users_and_refresh_tokens.py` containing `op.create_table("users", ...)` and `op.create_table("refresh_tokens", ...)`. Rename it to `alembic/versions/0001_initial.py` and set `revision = "0001"`, `down_revision = None` for a stable, readable history.

- [ ] **Step 5: Apply and verify the migration**

Run:
```bash
DATABASE_URL=postgresql+asyncpg://ielts:ielts@localhost:5432/ielts \
  REDIS_URL=redis://localhost:6379/0 JWT_SECRET=x \
  R2_ENDPOINT_URL=x R2_ACCESS_KEY_ID=x R2_SECRET_ACCESS_KEY=x R2_BUCKET=x \
  OPENROUTER_API_KEY=x \
  uv run alembic upgrade head
```
Expected: "Running upgrade -> 0001, initial users and refresh tokens". Then tear down: `docker rm -f ielts-pg-tmp`.

- [ ] **Step 6: Extend `tests/conftest.py` with DB fixtures**

`tests/conftest.py` already exists (created in Task 1) with the env-bootstrap block at the top. **Keep that block**; add the imports below to the existing import section (do not duplicate `import os`) and append the fixtures. Final file:

```python
# tests/conftest.py
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

# Set required env BEFORE any test imports a core.* module that calls
# get_settings() at import time. Placeholder values are fine: the real DB tests
# use the testcontainers _engine fixture / get_db override, not core.db.engine.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost:5432/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("R2_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("R2_ACCESS_KEY_ID", "x")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "x")
os.environ.setdefault("R2_BUCKET", "x")
os.environ.setdefault("OPENROUTER_API_KEY", "x")


@pytest.fixture(scope="session")
def postgres_url() -> AsyncIterator[str]:
    with PostgresContainer("postgres:15", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest_asyncio.fixture(scope="session")
async def _engine(postgres_url: str):
    from core.models import Base

    engine = create_async_engine(postgres_url, connect_args={"statement_cache_size": 0})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
```

Note: tests bind to the testcontainers Postgres through the `_engine`/`db_session` fixtures and the `get_db` override (Task 12), **not** through `core.db.engine` (which stays on the cached placeholder URL). This is intentional — nothing under test reads `core.db.engine`.

- [ ] **Step 7: Replace `tests/test_db.py` with the live test and run the DB suite**

```python
# tests/test_db.py
from sqlalchemy import text


async def test_engine_executes_select(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1
```

Run: `uv run pytest tests/test_db.py tests/test_models.py -v`
Expected: PASS (testcontainers pulls postgres:15 on first run — Docker must be running).

- [ ] **Step 8: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add alembic.ini alembic tests/conftest.py tests/test_db.py
git commit -m "feat: async alembic env + first migration + test fixtures"
```

---

## Task 8: Auth & user schemas

**Files:**
- Create: `core/schemas/auth.py`
- Create: `core/schemas/user.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `core/schemas/auth.py`: `RegisterRequest{email: EmailStr, password: str}` (password min_length 8), `LoginRequest{email: EmailStr, password: str}`, `RefreshRequest{refresh_token: str}`, `TokenResponse{access_token: str, refresh_token: str, token_type: Literal["bearer"] = "bearer"}`.
  - `core/schemas/user.py`: `UserRead{id: uuid.UUID, email: EmailStr, plan: str, created_at: datetime}` with `model_config = ConfigDict(from_attributes=True)`.

Note: `EmailStr` requires the `email-validator` package — add `"pydantic[email]>=2.10"` to deps in this task (update `pyproject.toml`, re-run `uv sync`).

- [ ] **Step 1: Add the email-validator extra**

Edit `pyproject.toml` dependencies: change `"pydantic>=2.10"` to `"pydantic[email]>=2.10"`. Run: `uv sync`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_schemas.py
import pytest
from pydantic import ValidationError

from core.schemas.auth import RegisterRequest, TokenResponse


def test_register_requires_valid_email():
    with pytest.raises(ValidationError):
        RegisterRequest(email="not-an-email", password="longenough")


def test_register_password_min_length():
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="short")


def test_token_response_defaults_bearer():
    t = TokenResponse(access_token="a", refresh_token="r")
    assert t.token_type == "bearer"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.schemas.auth'`.

- [ ] **Step 4: Write the schemas**

```python
# core/schemas/auth.py
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
```

```python
# core/schemas/user.py
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    plan: str
    created_at: datetime
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add pyproject.toml uv.lock core/schemas tests/test_schemas.py
git commit -m "feat: auth and user pydantic schemas"
```

---

## Task 9: Security primitives (argon2 + JWT)

**Files:**
- Create: `core/services/security.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces:
  - `hash_password(plain: str) -> str`
  - `verify_password(plain: str, hashed: str) -> bool`
  - `create_access_token(subject: str) -> str` — JWT with `sub`, `type="access"`, `exp` = now + `access_token_ttl_seconds`, `jti`.
  - `create_refresh_token(subject: str) -> tuple[str, str, datetime]` — returns `(token, jti, expires_at)`; JWT with `sub`, `type="refresh"`, `exp`, `jti`.
  - `decode_token(token: str) -> dict[str, object]` — decodes/validates; raises `AuthError` on failure or expiry.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_security.py
import pytest

from core.exceptions import AuthError
from core.services.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_round_trip():
    h = hash_password("hunter2pw")
    assert h != "hunter2pw"
    assert verify_password("hunter2pw", h) is True
    assert verify_password("wrong", h) is False


def test_access_token_round_trip():
    token = create_access_token("user-123")
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_refresh_token_has_jti():
    token, jti, expires_at = create_refresh_token("user-123")
    payload = decode_token(token)
    assert payload["jti"] == jti
    assert payload["type"] == "refresh"


def test_decode_rejects_garbage():
    with pytest.raises(AuthError):
        decode_token("not.a.jwt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.services.security'`.

- [ ] **Step 3: Write the implementation**

```python
# core/services/security.py
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt

from core.exceptions import AuthError
from core.settings import get_settings

_settings = get_settings()
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(subject: str) -> str:
    expire = _now() + timedelta(seconds=_settings.access_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


def create_refresh_token(subject: str) -> tuple[str, str, datetime]:
    jti = str(uuid.uuid4())
    expire = _now() + timedelta(seconds=_settings.refresh_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "refresh",
        "exp": expire,
        "jti": jti,
    }
    token = jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)
    return token, jti, expire


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token, _settings.jwt_secret, algorithms=[_settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise AuthError("invalid or expired token") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_security.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add core/services/security.py tests/test_security.py
git commit -m "feat: argon2 password hashing + JWT token helpers"
```

---

## Task 10: Auth service (register / login / refresh / logout / me)

**Files:**
- Create: `core/services/auth.py`
- Test: `tests/test_auth_service.py`

**Interfaces:**
- Consumes: `AsyncSession`, `User`, `RefreshToken`, security helpers (Task 9), `TokenResponse` (Task 8), `AppError` subclasses (Task 5).
- Produces (all async, all take `session: AsyncSession` as first arg):
  - `register(session, email: str, password: str) -> User` — raises `ConflictError` if email exists.
  - `login(session, email: str, password: str) -> TokenResponse` — raises `AuthError` on bad creds; persists a `RefreshToken` row.
  - `refresh(session, refresh_token: str) -> TokenResponse` — validates token, checks the `jti` row is not revoked/expired, **revokes the old row** (rotation), issues a new pair. Raises `AuthError` on reuse/invalid.
  - `logout(session, refresh_token: str) -> None` — revokes the token's `jti` row (idempotent).
  - `get_user_by_id(session, user_id: uuid.UUID) -> User` — raises `NotFoundError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_service.py
import pytest

from core.exceptions import AuthError, ConflictError
from core.services import auth


async def test_register_then_login(db_session):
    user = await auth.register(db_session, "x@y.com", "password123")
    assert user.email == "x@y.com"
    assert user.plan == "free"

    tokens = await auth.login(db_session, "x@y.com", "password123")
    assert tokens.access_token
    assert tokens.refresh_token


async def test_register_duplicate_email_conflicts(db_session):
    await auth.register(db_session, "dup@y.com", "password123")
    with pytest.raises(ConflictError):
        await auth.register(db_session, "dup@y.com", "password123")


async def test_login_bad_password_raises(db_session):
    await auth.register(db_session, "z@y.com", "password123")
    with pytest.raises(AuthError):
        await auth.login(db_session, "z@y.com", "wrongpass")


async def test_refresh_rotates_and_blocks_reuse(db_session):
    await auth.register(db_session, "r@y.com", "password123")
    tokens = await auth.login(db_session, "r@y.com", "password123")

    new_tokens = await auth.refresh(db_session, tokens.refresh_token)
    assert new_tokens.refresh_token != tokens.refresh_token

    # reusing the old (now revoked) refresh token must fail
    with pytest.raises(AuthError):
        await auth.refresh(db_session, tokens.refresh_token)


async def test_logout_revokes_token(db_session):
    await auth.register(db_session, "l@y.com", "password123")
    tokens = await auth.login(db_session, "l@y.com", "password123")
    await auth.logout(db_session, tokens.refresh_token)
    with pytest.raises(AuthError):
        await auth.refresh(db_session, tokens.refresh_token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth_service.py -v`
Expected: FAIL — `ImportError` / `AttributeError: module 'core.services.auth' has no attribute 'register'`.

- [ ] **Step 3: Write the implementation**

```python
# core/services/auth.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AuthError, ConflictError, NotFoundError
from core.models import RefreshToken, User
from core.schemas.auth import TokenResponse
from core.services.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


async def register(session: AsyncSession, email: str, password: str) -> User:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise ConflictError("email already registered")
    user = User(email=email, password_hash=hash_password(password), plan="free")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _issue_tokens(session: AsyncSession, user: User) -> TokenResponse:
    access = create_access_token(str(user.id))
    refresh_token, jti, expires_at = create_refresh_token(str(user.id))
    session.add(
        RefreshToken(user_id=user.id, jti=jti, expires_at=expires_at)
    )
    await session.commit()
    return TokenResponse(access_token=access, refresh_token=refresh_token)


async def login(session: AsyncSession, email: str, password: str) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("invalid credentials")
    return await _issue_tokens(session, user)


async def refresh(session: AsyncSession, refresh_token: str) -> TokenResponse:
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise AuthError("not a refresh token")
    jti = str(payload["jti"])
    row = await session.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if row is None or row.revoked_at is not None:
        raise AuthError("refresh token revoked or unknown")
    if row.expires_at <= datetime.now(timezone.utc):
        raise AuthError("refresh token expired")

    row.revoked_at = datetime.now(timezone.utc)  # rotation: revoke on use
    user = await get_user_by_id(session, row.user_id)
    return await _issue_tokens(session, user)


async def logout(session: AsyncSession, refresh_token: str) -> None:
    try:
        payload = decode_token(refresh_token)
    except AuthError:
        return  # idempotent: nothing to revoke
    jti = str(payload.get("jti", ""))
    row = await session.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        await session.commit()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("user not found")
    return user
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auth_service.py -v`
Expected: PASS (all five).

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add core/services/auth.py tests/test_auth_service.py
git commit -m "feat: auth service with refresh-token rotation"
```

---

## Task 11: FastAPI dependencies

**Files:**
- Create: `api/deps.py`
- Test: covered by router tests (Task 13) and the e2e flow (Task 17); no standalone test file.

**Interfaces:**
- Consumes: `async_session_factory` (Task 3), `decode_token` (Task 9), `get_user_by_id` (Task 10), `AuthError` (Task 5).
- Produces:
  - `get_db() -> AsyncIterator[AsyncSession]`.
  - `CurrentUser` = `Annotated[User, Depends(get_current_user)]`.
  - `get_current_user(...)` — reads `Authorization: Bearer <token>`, decodes, verifies `type == "access"`, loads the user; raises `AuthError` on any failure.

- [ ] **Step 1: Write `api/deps.py`**

```python
# api/deps.py
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import async_session_factory
from core.exceptions import AuthError
from core.models import User
from core.services.auth import get_user_by_id
from core.services.security import decode_token

_bearer = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    session: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None:
        raise AuthError("missing bearer token")
    payload = decode_token(creds.credentials)
    if payload.get("type") != "access":
        raise AuthError("not an access token")
    import uuid

    return await get_user_by_id(session, uuid.UUID(str(payload["sub"])))


CurrentUser = Annotated[User, Depends(get_current_user)]
```

- [ ] **Step 2: Verify it imports and type-checks**

Run: `uv run python -c "import api.deps" && uv run mypy api/deps.py`
Expected: no output from the import; mypy "Success".

- [ ] **Step 3: Lint, format, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add api/deps.py
git commit -m "feat: FastAPI db + current-user dependencies"
```

---

## Task 12: App factory + health router

**Files:**
- Create: `api/routers/health.py`
- Create: `api/main.py`
- Modify: `tests/conftest.py` (add `app_client` fixture)
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `register_exception_handlers` (Task 5), `configure_logging` + `RequestIdMiddleware` (Task 4), `get_db` (Task 11), `get_settings` (Task 2), Redis client.
- Produces:
  - `api/routers/health.py`: `router` with `GET /health` (tag `"health"`, `operation_id="health_check"`) returning `{"status": "ok", "db": "ok", "redis": "ok"}`; pings DB (`SELECT 1`) and Redis (`PING`).
  - `api/main.py`: `create_app() -> FastAPI` and module-level `app = create_app()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health.py
async def test_health_ok(app_client):
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
```

- [ ] **Step 2: Add the `app_client` fixture to `tests/conftest.py`**

Add these two imports to the **existing** import block at the top of `tests/conftest.py` (do NOT re-import `pytest_asyncio` — it is already imported from Task 7; a duplicate import trips ruff F811):

```python
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
```

Then append the fixture:

```python
@pytest_asyncio.fixture
async def app_client(_engine):
    from api.deps import get_db
    from api.main import create_app
    from core.db import async_session_factory

    # Bind the app's get_db to the testcontainers engine's session factory.
    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
```

Note: Redis is not available in CI unless started. For the health test, the Redis ping is allowed to be skipped when `REDIS_URL` is unreachable — see Step 3's `try/except` which reports `"redis": "down"` without failing the endpoint. The assertion in Step 1 only checks `db == "ok"`.

- [ ] **Step 3: Write `api/routers/health.py`**

```python
# api/routers/health.py
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter
from sqlalchemy import text

from api.deps import DbSession
from core.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", operation_id="health_check")
async def health_check(session: DbSession) -> dict[str, Any]:
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"

    redis_status = "ok"
    try:
        client = aioredis.from_url(get_settings().redis_url)
        await client.ping()
        await client.aclose()
    except Exception:
        redis_status = "down"

    overall = "ok" if db_status == "ok" else "degraded"
    return {"status": overall, "db": db_status, "redis": redis_status}
```

- [ ] **Step 4: Write `api/main.py` (health only — auth router is wired in Task 13)**

```python
# api/main.py
from fastapi import FastAPI

from api.routers import health as health_router
from core.exceptions import register_exception_handlers
from core.logging import RequestIdMiddleware, configure_logging

OPENAPI_TAGS = [
    {"name": "auth", "description": "Registration, login, token lifecycle."},
    {"name": "health", "description": "Liveness and readiness checks."},
]


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="IELTS Backend", version="0.1.0", openapi_tags=OPENAPI_TAGS)
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router.router)
    return app


app = create_app()
```

This file is complete and runnable as-is for Task 12. Task 13 adds the auth import and `include_router` line via an explicit edit — no commenting out required.

- [ ] **Step 5: Run the health test**

Run: `uv run pytest tests/test_health.py -v`
Expected: PASS (db "ok"; redis may be "down" in a Redis-less environment, which the test tolerates).

- [ ] **Step 6: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add api/routers/health.py api/main.py tests/conftest.py tests/test_health.py
git commit -m "feat: app factory, health endpoint, app test client"
```

---

## Task 13: Auth router

**Files:**
- Create: `api/routers/auth.py`
- Modify: `api/main.py` (wire the auth router)
- Test: `tests/test_auth_router.py`

**Interfaces:**
- Consumes: auth service (Task 10), schemas (Task 8), `DbSession` + `CurrentUser` (Task 11).
- Produces: `router` (`APIRouter(prefix="/auth", tags=["auth"])`) with:
  - `POST /auth/register` → `UserRead` (201), `operation_id="auth_register"`.
  - `POST /auth/login` → `TokenResponse`, `operation_id="auth_login"`.
  - `POST /auth/refresh` → `TokenResponse`, `operation_id="auth_refresh"`.
  - `POST /auth/logout` → 204, `operation_id="auth_logout"`.
  - `GET /auth/me` → `UserRead`, `operation_id="auth_me"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_router.py
async def test_register_returns_user(app_client):
    resp = await app_client.post(
        "/auth/register", json={"email": "new@u.com", "password": "password123"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new@u.com"
    assert body["plan"] == "free"
    assert "id" in body


async def test_login_and_me(app_client):
    await app_client.post(
        "/auth/register", json={"email": "me@u.com", "password": "password123"}
    )
    login = await app_client.post(
        "/auth/login", json={"email": "me@u.com", "password": "password123"}
    )
    assert login.status_code == 200
    access = login.json()["access_token"]

    me = await app_client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == "me@u.com"


async def test_me_requires_auth(app_client):
    resp = await app_client.get("/auth/me")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth_router.py -v`
Expected: FAIL — endpoints return 404 (the auth router is not yet created or wired into `api/main.py`).

- [ ] **Step 3: Write `api/routers/auth.py`**

```python
# api/routers/auth.py
from fastapi import APIRouter, status

from api.deps import CurrentUser, DbSession
from core.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from core.schemas.user import UserRead
from core.services import auth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="auth_register",
)
async def register(body: RegisterRequest, session: DbSession) -> UserRead:
    user = await auth.register(session, body.email, body.password)
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenResponse, operation_id="auth_login")
async def login(body: LoginRequest, session: DbSession) -> TokenResponse:
    return await auth.login(session, body.email, body.password)


@router.post("/refresh", response_model=TokenResponse, operation_id="auth_refresh")
async def refresh(body: RefreshRequest, session: DbSession) -> TokenResponse:
    return await auth.refresh(session, body.refresh_token)


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="auth_logout"
)
async def logout(body: RefreshRequest, session: DbSession) -> None:
    await auth.logout(session, body.refresh_token)


@router.get("/me", response_model=UserRead, operation_id="auth_me")
async def me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)
```

- [ ] **Step 3b: Wire the auth router into `api/main.py`**

Add the import alongside the existing `health` import:

```python
from api.routers import auth as auth_router
```

And add the include line after `app.include_router(health_router.router)` in `create_app()`:

```python
    app.include_router(auth_router.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auth_router.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add api/routers/auth.py api/main.py tests/test_auth_router.py
git commit -m "feat: auth router (register/login/refresh/logout/me)"
```

---

## Task 14: ARQ worker

**Files:**
- Create: `worker/main.py`
- Modify: `worker/tasks/__init__.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `get_settings()` (Task 2), `get_logger` (Task 4).
- Produces:
  - `worker/tasks/__init__.py`: `async def healthcheck(ctx: dict[str, Any]) -> str` returning `"ok"`.
  - `worker/main.py`: `WorkerSettings` class with `functions = [healthcheck]` and `redis_settings = RedisSettings.from_dsn(get_settings().redis_url)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker.py
from worker.tasks import healthcheck


async def test_healthcheck_returns_ok():
    result = await healthcheck({})
    assert result == "ok"


def test_worker_settings_has_functions():
    from worker.main import WorkerSettings

    assert healthcheck in WorkerSettings.functions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worker.py -v`
Expected: FAIL — `ImportError: cannot import name 'healthcheck' from 'worker.tasks'`.

- [ ] **Step 3: Write the worker**

```python
# worker/tasks/__init__.py
from typing import Any

from core.logging import get_logger

log = get_logger("worker.tasks")


async def healthcheck(ctx: dict[str, Any]) -> str:
    log.info("healthcheck.run")
    return "ok"
```

```python
# worker/main.py
from arq.connections import RedisSettings

from core.settings import get_settings
from worker.tasks import healthcheck


class WorkerSettings:
    functions = [healthcheck]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_worker.py -v`
Expected: PASS (both).

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
git add worker/main.py worker/tasks/__init__.py tests/test_worker.py
git commit -m "feat: ARQ worker settings + healthcheck task"
```

---

## Task 15: Docker image + Docker Compose

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: the whole app.
- Produces: a single image that can run either `uvicorn api.main:app` (api) or `arq worker.main.WorkerSettings` (worker); a compose stack with postgres + redis + minio for local parity.

- [ ] **Step 1: Write `.dockerignore`**

```
.venv/
__pycache__/
.git/
.mypy_cache/
.pytest_cache/
.ruff_cache/
tests/
docs/
*.md
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY core ./core
COPY api ./api
COPY worker ./worker
COPY alembic.ini ./
COPY alembic ./alembic
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# API by default; the worker process overrides the command (see compose / Fly).
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: ielts
      POSTGRES_PASSWORD: ielts
      POSTGRES_DB: ielts
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7
    command: ["redis-server", "--appendonly", "yes"]
    ports:
      - "6379:6379"
    volumes:
      - redisdata:/data

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - miniodata:/data

volumes:
  pgdata:
  redisdata:
  miniodata:
```

- [ ] **Step 4: Verify the stack boots and the image builds**

Run:
```bash
docker compose up -d
docker build -t ielts-backend:dev .
```
Expected: compose reports postgres/redis/minio healthy/running; the image builds without error. Tear down with `docker compose down` when done verifying.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore
git commit -m "chore: dockerfile + local compose (postgres/redis/minio)"
```

---

## Task 16: CI pipeline

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `uv`, ruff, mypy, pytest. Tests need Docker for testcontainers — GitHub-hosted Ubuntu runners provide Docker by default.
- Produces: a CI job that runs `uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest` on push and PR.

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install 3.11

      - name: Install dependencies
        run: uv sync --frozen

      - name: Ruff lint
        run: uv run ruff check .

      - name: Ruff format check
        run: uv run ruff format --check .

      - name: Mypy (strict)
        run: uv run mypy .

      - name: Pytest
        run: uv run pytest -v
```

Note: testcontainers uses the Docker daemon present on `ubuntu-latest`; no `services:` block is needed because the Postgres container is started by the test fixture itself.

- [ ] **Step 2: Verify the workflow file is valid YAML and mirrors local commands**

Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('valid')"`
Expected: `valid`. (`pyyaml` is available transitively; if not, install with `uv pip install pyyaml` for this check only.)

- [ ] **Step 3: Run the full local suite exactly as CI will**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -v`
Expected: all pass (Docker running for testcontainers).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: uv sync + ruff + mypy + pytest on push/PR"
```

---

## Task 17: End-to-end auth flow integration test

**Files:**
- Create: `tests/test_auth_flow_e2e.py`

**Interfaces:**
- Consumes: `app_client` fixture (Task 12).
- Produces: a single test exercising the full Foundation acceptance criterion: register → login → access protected route → refresh → use new access token → logout → old refresh rejected.

- [ ] **Step 1: Write the e2e test**

```python
# tests/test_auth_flow_e2e.py
async def test_full_auth_lifecycle(app_client):
    # register
    reg = await app_client.post(
        "/auth/register", json={"email": "flow@u.com", "password": "password123"}
    )
    assert reg.status_code == 201

    # login
    login = await app_client.post(
        "/auth/login", json={"email": "flow@u.com", "password": "password123"}
    )
    assert login.status_code == 200
    tokens = login.json()

    # access protected route
    me = await app_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200

    # refresh -> new pair
    refreshed = await app_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # new access token works
    me2 = await app_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {new_tokens['access_token']}"}
    )
    assert me2.status_code == 200

    # old refresh token now rejected (rotation)
    reused = await app_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401

    # logout revokes current refresh token
    logout = await app_client.post(
        "/auth/logout", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert logout.status_code == 204

    after_logout = await app_client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert after_logout.status_code == 401
```

- [ ] **Step 2: Run the e2e test**

Run: `uv run pytest tests/test_auth_flow_e2e.py -v`
Expected: PASS.

- [ ] **Step 3: Run the entire suite + full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -v`
Expected: all green — this is the Foundation acceptance bar.

- [ ] **Step 4: Commit**

```bash
git add tests/test_auth_flow_e2e.py
git commit -m "test: end-to-end auth lifecycle"
```

---

## Self-Review

**Spec coverage (§8 acceptance criteria):**
- "api and worker both boot; worker connects to Redis, runs healthcheck task" → Tasks 12, 14. ✅
- "Full auth flow: register → login → protected route → refresh → logout" → Tasks 10, 13, 17. ✅
- "`GET /health` green (checks DB + Redis)" → Task 12. ✅
- "`GET /docs` OpenAPI with explicit tags + stable operation IDs" → Global Constraints + Tasks 12, 13 (`tags=`, `operation_id=`). ✅
- "Schema changes via Alembic; first migration creates users + refresh tokens" → Task 7. ✅
- "CI green: uv sync, ruff check, ruff format --check, mypy strict, pytest" → Task 16. ✅
- Repo skeleton (settings, db, logging, exceptions, models, schemas, services, alembic, tests, Dockerfile, compose, pyproject, CI) → Tasks 1–16. ✅
- §9 open items resolved: UUIDv7 = `uuid-utils` (Task 3); API CORS omitted in Foundation (documented in plan intro); Redis hosting deferred to deploy phase, app consumes `REDIS_URL`. ✅

**Harness bootstrap:** `core/db.py` and `core/services/security.py` call `get_settings()` at import time, and `Settings` fields are required. `tests/conftest.py` is therefore created in **Task 1** with an env-default block (including `DATABASE_URL`) so pytest can collect tests from Task 3 onward without a `ValidationError`; Tasks 7 and 12 extend the same file rather than recreate it. ✅

**Placeholder scan:** No "TBD"/"implement later"/"add error handling" — every code step has complete code and exact commands. ✅

**Type consistency:** `TokenResponse`, `UserRead`, `RegisterRequest`/`LoginRequest`/`RefreshRequest` used identically across schemas (Task 8), service (Task 10), router (Task 13). `decode_token`/`create_access_token`/`create_refresh_token` signatures consistent across security (Task 9), auth service (Task 10), deps (Task 11). `get_db`/`DbSession`/`CurrentUser` consistent across deps (Task 11) and routers (Tasks 12, 13). `healthcheck` consistent across worker task and `WorkerSettings` (Task 14). ✅
