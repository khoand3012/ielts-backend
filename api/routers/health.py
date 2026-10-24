from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from api.deps import DbSession

router = APIRouter(tags=["health"])


@router.get("/health", operation_id="health_check")
async def health_check(session: DbSession) -> dict[str, Any]:
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"

    return {"status": db_status, "db": db_status}
