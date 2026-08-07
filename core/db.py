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
