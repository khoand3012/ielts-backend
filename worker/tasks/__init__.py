from typing import Any

from core.logging import get_logger

log = get_logger("worker.tasks")


async def healthcheck(ctx: dict[str, Any]) -> str:
    log.info("healthcheck.run")
    return "ok"
