from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from core.models import Answer, Test, TestSession, TestVersion, User

MakeUser = Callable[..., Awaitable[tuple[User, str]]]


async def _test(db: AsyncSession) -> Test:
    test = Test(skill="reading", slug=f"t-{uuid7()}", title="T")
    db.add(test)
    await db.flush()
    return test


def _version(test: Test, version: int, status: str = "draft") -> TestVersion:
    return TestVersion(test_id=test.id, version=version, status=status, content={})


async def test_second_draft_for_same_test_rejected(db_session: AsyncSession) -> None:
    test = await _test(db_session)
    db_session.add_all([_version(test, 1), _version(test, 2)])
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_draft_plus_published_allowed(db_session: AsyncSession) -> None:
    test = await _test(db_session)
    db_session.add_all([_version(test, 1, "published"), _version(test, 2)])
    await db_session.flush()


async def test_version_numbers_unique_per_test(db_session: AsyncSession) -> None:
    test = await _test(db_session)
    db_session.add_all([_version(test, 1, "published"), _version(test, 1, "retired")])
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def _session_row(db: AsyncSession, user: User, mode: str = "exam") -> TestSession:
    test = await _test(db)
    version = _version(test, 1, "published")
    db.add(version)
    await db.flush()
    ts = TestSession(
        user_id=user.id,
        test_version_id=version.id,
        skill="reading",
        mode=mode,
        started_at=datetime.now(UTC),
    )
    db.add(ts)
    await db.flush()
    return ts


async def test_session_rejects_unknown_mode(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    with pytest.raises(IntegrityError):
        await _session_row(db_session, user, mode="sprint")


async def test_session_defaults(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await _session_row(db_session, user)
    await db_session.refresh(ts)
    assert ts.status == "in_progress"
    assert ts.request_manual_grading is False
    assert ts.deadline is None


async def test_answer_unique_per_question(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await _session_row(db_session, user)
    db_session.add_all(
        [
            Answer(test_session_id=ts.id, question_number=1, value="a"),
            Answer(test_session_id=ts.id, question_number=1, value="b"),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
