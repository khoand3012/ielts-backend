import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from core.exceptions import QuotaExceededError
from core.models import DailyUsage, Plan, Skill, User
from core.services.entitlements import check_and_consume, daily_limit

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
NOW = datetime(2026, 10, 24, 15, 30, tzinfo=UTC)


async def consume(db: AsyncSession, user: User, skill: Skill, now: datetime = NOW) -> None:
    try:
        await check_and_consume(db, user, skill, now)
        await db.commit()
    except QuotaExceededError:
        await db.rollback()
        raise


async def test_free_user_gets_three_writing_per_day(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    for _ in range(3):
        await consume(db_session, user, Skill.WRITING)
    with pytest.raises(QuotaExceededError) as exc:
        await consume(db_session, user, Skill.WRITING)
    assert exc.value.status_code == 429
    assert exc.value.code == "quota_exceeded"
    assert exc.value.extra["resets_at"] == "2026-10-25T00:00:00+00:00"


async def test_pools_are_separate(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    for _ in range(3):
        await consume(db_session, user, Skill.WRITING)
    await consume(db_session, user, Skill.SPEAKING)


async def test_new_day_resets(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    for _ in range(3):
        await consume(db_session, user, Skill.WRITING)
    await consume(db_session, user, Skill.WRITING, NOW + timedelta(days=1))


async def test_paid_limit_is_30(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    user.plan = Plan.PAID
    assert daily_limit(user, Skill.WRITING) == 30
    assert daily_limit(user, Skill.SPEAKING) == 30


def test_reading_has_no_limit() -> None:
    with pytest.raises(ValueError):
        daily_limit(User(plan="free"), Skill.READING)


async def test_failed_call_consumes_nothing(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    for _ in range(3):
        await consume(db_session, user, Skill.WRITING)
    with pytest.raises(QuotaExceededError):
        await consume(db_session, user, Skill.WRITING)
    row = await db_session.get(DailyUsage, (user.id, "writing", date(2026, 10, 24)))
    assert row is not None
    assert row.count == 3


async def test_concurrent_starts_take_the_last_slot_once(
    _engine: AsyncEngine, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as db:
        for _ in range(2):
            await consume(db, user, Skill.WRITING)

    async def attempt() -> bool:
        async with factory() as db:
            try:
                await consume(db, user, Skill.WRITING)
                return True
            except QuotaExceededError:
                return False

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == [False, True]
