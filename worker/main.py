from typing import ClassVar

from arq.connections import RedisSettings

from core.settings import get_settings
from worker.tasks import healthcheck


class WorkerSettings:
    functions: ClassVar = [healthcheck]
    redis_settings: ClassVar = RedisSettings.from_dsn(get_settings().redis_url)
