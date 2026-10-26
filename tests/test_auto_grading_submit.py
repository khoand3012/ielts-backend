from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from core.models import Module, SessionMode, Skill, TestSession, TestVersion, User
from core.schemas.session import AnswerInput
from core.services import sessions
from tests.helpers import correct_answers, publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
AFTER_DEADLINE = NOW + timedelta(hours=2)


class RecordingLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def exception(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


async def start(
    db: AsyncSession,
    user: User,
    skill: Skill,
    mode: SessionMode = SessionMode.EXAM,
    module: Module | None = None,
) -> TestSession:
    v = await publish_test(db, skill, module=module)
    return await sessions.start_session(db, user, v.test_id, mode, NOW)


async def answer(db: AsyncSession, user: User, ts: TestSession, values: dict[int, Any]) -> None:
    if values:
        inputs = [AnswerInput(question=q, value=v) for q, v in values.items()]
        await sessions.save_answers(db, user, ts.id, inputs, NOW)


async def stored(engine: AsyncEngine, ts: TestSession) -> TestSession:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        row = await db.get(TestSession, ts.id)
        assert row is not None
        return row


def first(skill: Skill, n: int) -> dict[int, Any]:
    return {q: a for q, a in correct_answers(skill).items() if q <= n}


async def test_submit_scores_reading_and_listening(
    db_session: AsyncSession, _engine: AsyncEngine, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    for skill in (Skill.READING, Skill.LISTENING):
        ts = await start(db_session, user, skill)
        await answer(db_session, user, ts, dict(correct_answers(skill)))
        done = await sessions.submit_session(db_session, user, ts.id, NOW)
        assert (done.raw_score, done.band) == (40, D("9.0"))
        row = await stored(_engine, ts)
        assert (row.raw_score, row.band) == (40, D("9.0"))


async def test_module_changes_band(
    db_session: AsyncSession, _engine: AsyncEngine, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    bands = []
    for module in (Module.ACADEMIC, Module.GENERAL_TRAINING):
        ts = await start(db_session, user, Skill.READING, module=module)
        await answer(db_session, user, ts, first(Skill.READING, 30))
        await sessions.submit_session(db_session, user, ts.id, NOW)
        row = await stored(_engine, ts)
        bands.append((row.raw_score, row.band))
    assert bands == [(30, D("7.0")), (30, D("6.0"))]


async def test_practice_partial_has_no_band(
    db_session: AsyncSession, _engine: AsyncEngine, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.LISTENING, SessionMode.PRACTICE)
    values = dict(correct_answers(Skill.LISTENING))
    del values[1]
    await answer(db_session, user, ts, values)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    row = await stored(_engine, ts)
    assert (row.raw_score, row.band) == (39, None)


async def test_empty_submissions(
    db_session: AsyncSession, _engine: AsyncEngine, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    exam = await start(db_session, user, Skill.READING)
    practice = await start(db_session, user, Skill.READING, SessionMode.PRACTICE)
    for ts in (exam, practice):
        await sessions.submit_session(db_session, user, ts.id, NOW)
    assert ((await stored(_engine, exam)).raw_score, (await stored(_engine, exam)).band) == (
        0,
        D("0.0"),
    )
    row = await stored(_engine, practice)
    assert (row.raw_score, row.band) == (0, None)


async def test_timeout_paths_score(
    db_session: AsyncSession, _engine: AsyncEngine, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    by_loop = await start(db_session, user, Skill.READING)
    on_read = await start(db_session, user, Skill.LISTENING)
    await sessions.auto_submit_expired(db_session, AFTER_DEADLINE)
    await sessions.get_session(db_session, user, on_read.id, AFTER_DEADLINE)
    for ts in (by_loop, on_read):
        row = await stored(_engine, ts)
        assert (row.status, row.submit_reason, row.raw_score) == ("submitted", "timeout", 0)


async def test_writing_is_not_scored(
    db_session: AsyncSession, _engine: AsyncEngine, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.WRITING)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    row = await stored(_engine, ts)
    assert (row.status, row.raw_score, row.band) == ("submitted", None, None)


async def test_scoring_failure_still_submits(
    db_session: AsyncSession,
    _engine: AsyncEngine,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(ts: TestSession, version: TestVersion, answers: dict[int, Any]) -> Any:
        raise RuntimeError("marking bug")

    log = RecordingLog()
    monkeypatch.setattr(sessions, "mark_session", boom)
    monkeypatch.setattr(sessions, "log", log)
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.READING)
    done = await sessions.submit_session(db_session, user, ts.id, NOW)
    assert done.status == "submitted"
    row = await stored(_engine, ts)
    assert (row.status, row.raw_score, row.band) == ("submitted", None, None)
    assert log.events == [("auto_grading_failed", {"session_id": str(ts.id)})]


async def test_batch_auto_submit_with_one_failure(
    db_session: AsyncSession,
    _engine: AsyncEngine,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await make_user()
    bad = await start(db_session, user, Skill.READING)
    good = await start(db_session, user, Skill.READING)
    real = sessions.mark_session

    def flaky(ts: TestSession, version: TestVersion, answers: dict[int, Any]) -> Any:
        if ts.id == bad.id:
            raise RuntimeError("marking bug")
        return real(ts, version, answers)

    monkeypatch.setattr(sessions, "mark_session", flaky)
    monkeypatch.setattr(sessions, "log", RecordingLog())
    assert await sessions.auto_submit_expired(db_session, AFTER_DEADLINE) >= 2
    bad_row, good_row = await stored(_engine, bad), await stored(_engine, good)
    assert (bad_row.status, bad_row.raw_score) == ("submitted", None)
    assert (good_row.status, good_row.raw_score, good_row.band) == ("submitted", 0, D("0.0"))
