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
        client = aioredis.from_url(get_settings().redis_url)  # type: ignore[no-untyped-call]
        await client.ping()
        await client.aclose()
    except Exception:
        redis_status = "down"

    overall = "ok" if db_status == "ok" else "degraded"
    return {"status": overall, "db": db_status, "redis": redis_status}
