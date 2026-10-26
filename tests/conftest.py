# tests/conftest.py
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer
from uuid_utils.compat import uuid7

if TYPE_CHECKING:
    from core.models import Role, User

# Set required env BEFORE any test imports a core.* module that calls
# get_settings() at import time. Placeholder values are fine: the real DB tests
# use the testcontainers _engine fixture / get_db override, not core.db.engine.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost:5432/x")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("R2_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("R2_ACCESS_KEY_ID", "x")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "x")
os.environ.setdefault("R2_BUCKET", "x")
os.environ.setdefault("OPENROUTER_API_KEY", "x")
# The app's auto-submit loop would use core.db's placeholder engine; tests drive it directly.
os.environ.setdefault("AUTO_SUBMIT_ENABLED", "false")
os.environ.setdefault("STARTUP_SCORING_ENABLED", "false")


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:15", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest_asyncio.fixture(scope="session")
async def _engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    from core.models import Base

    engine = create_async_engine(postgres_url, connect_args={"statement_cache_size": 0})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def app_client(_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    from api.deps import get_db
    from api.main import create_app

    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def make_user(
    _engine: AsyncEngine,
) -> Callable[..., Awaitable[tuple["User", str]]]:
    """Create a committed user with a unique email and the given role; return it with an
    access token."""
    from core.models import Role
    from core.services import auth
    from core.services.security import create_access_token

    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _make(role: "Role" = Role.STUDENT) -> tuple["User", str]:
        async with factory() as session:
            user = await auth.register(session, f"{role}-{uuid7()}@t.com", "password123")
            user.role = role
            await session.commit()
        return user, create_access_token(str(user.id))

    return _make
