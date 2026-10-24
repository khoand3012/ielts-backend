from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from core.exceptions import ConflictError, NotFoundError, QuotaExceededError
from core.models import SessionMode, Skill, Test, User
from core.services import catalog, sessions
from tests.helpers import publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


async def test_exam_session_has_deadline(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    for skill, minutes in ((Skill.READING, 60), (Skill.LISTENING, 32), (Skill.WRITING, 60)):
        v = await publish_test(db_session, skill)
        ts = await sessions.start_session(db_session, user, v.test_id, SessionMode.EXAM, NOW)
        assert ts.started_at == NOW
        assert ts.deadline == NOW + timedelta(minutes=minutes)
        assert (ts.skill, ts.mode, ts.status) == (skill, "exam", "in_progress")


async def test_practice_session_has_no_deadline(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    v = await publish_test(db_session, Skill.READING)
    ts = await sessions.start_session(db_session, user, v.test_id, SessionMode.PRACTICE, NOW)
    assert ts.deadline is None


async def test_writing_start_consumes_daily_limit(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    v = await publish_test(db_session, Skill.WRITING)
    for _ in range(3):
        await sessions.start_session(db_session, user, v.test_id, SessionMode.EXAM, NOW)
    with pytest.raises(QuotaExceededError):
        await sessions.start_session(db_session, user, v.test_id, SessionMode.EXAM, NOW)
    _, total = await sessions.list_sessions(db_session, user, limit=50, offset=0)
    assert total == 3


async def test_practice_counts_toward_limit(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    v = await publish_test(db_session, Skill.WRITING)
    for _ in range(3):
        await sessions.start_session(db_session, user, v.test_id, SessionMode.PRACTICE, NOW)
    with pytest.raises(QuotaExceededError):
        await sessions.start_session(db_session, user, v.test_id, SessionMode.PRACTICE, NOW)


async def test_reading_start_is_unlimited(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    v = await publish_test(db_session, Skill.READING)
    for _ in range(5):
        await sessions.start_session(db_session, user, v.test_id, SessionMode.EXAM, NOW)


async def test_speaking_cannot_start_yet(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    v = await publish_test(db_session, Skill.SPEAKING)
    with pytest.raises(ConflictError):
        await sessions.start_session(db_session, user, v.test_id, SessionMode.PRACTICE, NOW)


async def test_unpublished_test_cannot_start(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    test = Test(skill="reading", slug=f"draft-{uuid7()}", title="T")
    db_session.add(test)
    await db_session.commit()
    with pytest.raises(NotFoundError):
        await sessions.start_session(db_session, user, test.id, SessionMode.EXAM, NOW)


async def test_session_pinned_to_version(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    v1 = await publish_test(db_session, Skill.READING)
    test = await catalog.get_published_test(db_session, v1.test_id)
    old = await sessions.start_session(db_session, user, test.id, SessionMode.EXAM, NOW)
    v2 = await publish_test(db_session, Skill.READING, slug=test.slug)

    assert (await sessions.get_version(db_session, old)).id == v1.id
    new = await sessions.start_session(db_session, user, test.id, SessionMode.EXAM, NOW)
    assert (await sessions.get_version(db_session, new)).id == v2.id


async def test_get_and_list_own_sessions(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    v = await publish_test(db_session, Skill.READING)
    first = await sessions.start_session(db_session, user, v.test_id, SessionMode.EXAM, NOW)
    second = await sessions.start_session(db_session, user, v.test_id, SessionMode.PRACTICE, NOW)
    got = await sessions.get_session(db_session, user, first.id, NOW)
    assert got.id == first.id
    page, total = await sessions.list_sessions(db_session, user, limit=50, offset=0)
    assert total == 2
    assert [s.id for s in page] == [second.id, first.id]
    assert await sessions.get_answers(db_session, first) == {}


async def test_other_users_session_is_404(db_session: AsyncSession, make_user: MakeUser) -> None:
    owner, _ = await make_user()
    other, _ = await make_user()
    v = await publish_test(db_session, Skill.READING)
    ts = await sessions.start_session(db_session, owner, v.test_id, SessionMode.EXAM, NOW)
    with pytest.raises(NotFoundError):
        await sessions.get_session(db_session, other, ts.id, NOW)
