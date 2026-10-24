import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from core.models import SessionMode, Skill, TestSession, User
from core.services import sessions
from core.settings import get_settings
from tests.helpers import publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
LONG_AGO = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


async def expired_session(factory: async_sessionmaker[AsyncSession], user: User) -> TestSession:
    async with factory() as db:
        v = await publish_test(db, Skill.READING)
        return await sessions.start_session(db, user, v.test_id, SessionMode.EXAM, LONG_AGO)


async def wait_submitted(factory: async_sessionmaker[AsyncSession], ts: TestSession) -> str:
    for _ in range(40):
        async with factory() as db:
            row = await db.get(TestSession, ts.id)
            assert row is not None
            if row.status == "submitted":
                return row.status
        await asyncio.sleep(0.05)
    return "in_progress"


async def run_loop_until_submitted(
    factory: async_sessionmaker[AsyncSession], ts: TestSession
) -> str:
    task = asyncio.create_task(sessions.run_auto_submit_loop(factory, 0.05))
    try:
        return await wait_submitted(factory, ts)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_loop_submits_expired_sessions(_engine: AsyncEngine, make_user: MakeUser) -> None:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    user, _ = await make_user()
    ts = await expired_session(factory, user)
    assert await run_loop_until_submitted(factory, ts) == "submitted"


async def test_loop_survives_errors(
    _engine: AsyncEngine, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    user, _ = await make_user()
    ts = await expired_session(factory, user)
    real = sessions.auto_submit_expired
    calls = 0

    async def flaky(db: AsyncSession, now: datetime | None = None) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database blip")
        return await real(db, now)

    monkeypatch.setattr(sessions, "auto_submit_expired", flaky)
    assert await run_loop_until_submitted(factory, ts) == "submitted"
    assert calls >= 2


async def test_lifespan_starts_and_stops_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.main

    started = asyncio.Event()
    cancelled: list[bool] = []

    async def fake_loop(factory: Any, interval: float) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(get_settings(), "auto_submit_enabled", True)
    monkeypatch.setattr(api.main, "run_auto_submit_loop", fake_loop)
    async with LifespanManager(api.main.create_app()):
        await asyncio.wait_for(started.wait(), timeout=1)
    assert cancelled == [True]


async def test_lifespan_skips_loop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.main

    started: list[bool] = []

    async def fake_loop(factory: Any, interval: float) -> None:
        started.append(True)

    monkeypatch.setattr(get_settings(), "auto_submit_enabled", False)
    monkeypatch.setattr(api.main, "run_auto_submit_loop", fake_loop)
    async with LifespanManager(api.main.create_app()):
        await asyncio.sleep(0.05)
    assert started == []
