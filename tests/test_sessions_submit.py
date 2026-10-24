import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from core.exceptions import SessionClosedError
from core.models import SessionMode, Skill, TestSession, User
from core.schemas.session import AnswerInput
from core.services import sessions
from tests.helpers import publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
GRACE = timedelta(seconds=30)


@pytest.fixture
def hook_calls(monkeypatch: pytest.MonkeyPatch) -> list[TestSession]:
    calls: list[TestSession] = []

    async def hook(db: AsyncSession, ts: TestSession) -> None:
        calls.append(ts)

    monkeypatch.setattr(sessions, "on_session_submitted", hook)
    return calls


async def start(
    db: AsyncSession, user: User, skill: Skill, mode: SessionMode = SessionMode.EXAM
) -> TestSession:
    v = await publish_test(db, skill)
    return await sessions.start_session(db, user, v.test_id, mode, NOW)


def after_grace(ts: TestSession, extra: timedelta = timedelta(seconds=1)) -> datetime:
    assert ts.deadline is not None
    return ts.deadline + GRACE + extra


async def test_student_submit_sets_fields(
    db_session: AsyncSession, make_user: MakeUser, hook_calls: list[TestSession]
) -> None:
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.READING)
    done = await sessions.submit_session(db_session, user, ts.id, NOW + timedelta(minutes=5))
    assert (done.status, done.submit_reason, done.grading_method) == (
        "submitted",
        "student",
        "auto",
    )
    assert done.submitted_at == NOW + timedelta(minutes=5)
    assert [c.id for c in hook_calls] == [ts.id]


async def test_writing_grading_method_follows_toggle(
    db_session: AsyncSession, make_user: MakeUser, hook_calls: list[TestSession]
) -> None:
    user, _ = await make_user()
    manual = await start(db_session, user, Skill.WRITING)
    await sessions.set_manual_grading(db_session, user, manual.id, True, NOW)
    assert (await sessions.submit_session(db_session, user, manual.id, NOW)).grading_method == (
        "human"
    )
    ai = await start(db_session, user, Skill.WRITING)
    assert (await sessions.submit_session(db_session, user, ai.id, NOW)).grading_method == "ai"


async def test_double_submit_is_409_and_hook_runs_once(
    db_session: AsyncSession, make_user: MakeUser, hook_calls: list[TestSession]
) -> None:
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.READING)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    with pytest.raises(SessionClosedError):
        await sessions.submit_session(db_session, user, ts.id, NOW)
    assert len(hook_calls) == 1


async def test_submit_races_auto_submit(
    _engine: AsyncEngine, make_user: MakeUser, hook_calls: list[TestSession]
) -> None:
    user, _ = await make_user()
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as db:
        ts = await start(db, user, Skill.WRITING)
    late = after_grace(ts)

    async def student() -> None:
        async with factory() as db:
            try:
                await sessions.submit_session(db, user, ts.id, late)
            except SessionClosedError:
                pass

    async def sweep() -> None:
        async with factory() as db:
            await sessions.auto_submit_expired(db, late)

    await asyncio.gather(student(), sweep())
    assert [c.id for c in hook_calls].count(ts.id) == 1
    async with factory() as db:
        reloaded = await db.get(TestSession, ts.id)
        assert reloaded is not None
        assert (reloaded.status, reloaded.submit_reason) == ("submitted", "timeout")


async def test_save_within_grace_accepted(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.READING)
    assert ts.deadline is not None
    saved = await sessions.save_answers(
        db_session,
        user,
        ts.id,
        [AnswerInput(question=1, value=["TRUE"])],
        ts.deadline + timedelta(seconds=20),
    )
    assert saved == {1: ["TRUE"]}


async def test_save_after_grace_submits_and_409(
    _engine: AsyncEngine, make_user: MakeUser, hook_calls: list[TestSession]
) -> None:
    user, _ = await make_user()
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as db:
        ts = await start(db, user, Skill.READING)
        await sessions.save_answers(db, user, ts.id, [AnswerInput(question=1, value=["TRUE"])], NOW)
        with pytest.raises(SessionClosedError):
            await sessions.save_answers(
                db, user, ts.id, [AnswerInput(question=2, value=["FALSE"])], after_grace(ts)
            )
    async with factory() as db:
        reloaded = await db.get(TestSession, ts.id)
        assert reloaded is not None
        assert (reloaded.status, reloaded.submit_reason) == ("submitted", "timeout")
        assert await sessions.get_answers(db, reloaded) == {1: ["TRUE"]}
    assert [c.id for c in hook_calls] == [ts.id]


async def test_toggle_after_grace_submits_and_409(
    _engine: AsyncEngine, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as db:
        ts = await start(db, user, Skill.WRITING)
        with pytest.raises(SessionClosedError):
            await sessions.set_manual_grading(db, user, ts.id, True, after_grace(ts))
    async with factory() as db:
        reloaded = await db.get(TestSession, ts.id)
        assert reloaded is not None
        assert (reloaded.status, reloaded.grading_method) == ("submitted", "ai")


async def test_auto_submit_skips_practice_and_unexpired(
    db_session: AsyncSession, make_user: MakeUser, hook_calls: list[TestSession]
) -> None:
    user, _ = await make_user()
    practice = await start(db_session, user, Skill.READING, SessionMode.PRACTICE)
    listening = await start(db_session, user, Skill.LISTENING)  # 32 min
    reading = await start(db_session, user, Skill.READING)  # 60 min
    now = after_grace(listening)
    assert await sessions.auto_submit_expired(db_session, now) >= 1
    for ts in (practice, listening, reading):
        await db_session.refresh(ts)
    assert (practice.status, listening.status, reading.status) == (
        "in_progress",
        "submitted",
        "in_progress",
    )
    assert listening.submit_reason == "timeout"
    assert listening.id in [c.id for c in hook_calls]


async def test_get_session_submits_expired(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.READING)
    got = await sessions.get_session(db_session, user, ts.id, after_grace(ts))
    assert (got.status, got.submit_reason) == ("submitted", "timeout")


async def test_student_submit_after_grace_is_timeout(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.READING)
    done = await sessions.submit_session(db_session, user, ts.id, after_grace(ts))
    assert done.submit_reason == "timeout"
