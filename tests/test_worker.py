from worker.tasks import healthcheck


async def test_healthcheck_returns_ok() -> None:
    result = await healthcheck({})
    assert result == "ok"


def test_worker_settings_has_functions() -> None:
    from worker.main import WorkerSettings

    assert healthcheck in WorkerSettings.functions
