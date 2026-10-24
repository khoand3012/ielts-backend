"""A student's attempt at one test version: start, read, answers, submission."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError, QuotaExceededError
from core.models import Answer, SessionMode, Skill, TestSession, TestVersion, User
from core.services import catalog
from core.services.entitlements import check_and_consume
from core.settings import get_settings

_LIMITED_SKILLS = (Skill.WRITING, Skill.SPEAKING)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def exam_time_limit(skill: Skill) -> timedelta:
    settings = get_settings()
    minutes = {
        Skill.READING: settings.reading_time_limit_minutes,
        Skill.LISTENING: settings.listening_time_limit_minutes,
        Skill.WRITING: settings.writing_time_limit_minutes,
        Skill.SPEAKING: settings.speaking_time_limit_minutes,
    }[skill]
    return timedelta(minutes=minutes)


async def start_session(
    db: AsyncSession,
    user: User,
    test_id: uuid.UUID,
    mode: SessionMode,
    now: datetime | None = None,
) -> TestSession:
    now = _now(now)
    test = await catalog.get_published_test(db, test_id)
    skill = Skill(test.skill)
    if skill == Skill.SPEAKING:
        raise ConflictError("speaking tests can't be started until recording uploads exist")
    try:
        if skill in _LIMITED_SKILLS:
            await check_and_consume(db, user, skill, now)
        ts = TestSession(
            user_id=user.id,
            test_version_id=test.current_version_id,
            skill=skill,
            mode=mode,
            started_at=now,
            deadline=now + exam_time_limit(skill) if mode == SessionMode.EXAM else None,
        )
        db.add(ts)
        await db.commit()
    except QuotaExceededError:
        await db.rollback()
        raise
    await db.refresh(ts)
    return ts


async def _owned(db: AsyncSession, user: User, session_id: uuid.UUID) -> TestSession:
    ts = await db.get(TestSession, session_id)
    if ts is None or ts.user_id != user.id:
        raise NotFoundError("session not found")
    return ts


async def get_session(
    db: AsyncSession, user: User, session_id: uuid.UUID, now: datetime | None = None
) -> TestSession:
    return await _owned(db, user, session_id)


async def list_sessions(
    db: AsyncSession, user: User, *, limit: int = 50, offset: int = 0
) -> tuple[list[TestSession], int]:
    stmt = select(TestSession).where(TestSession.user_id == user.id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = await db.scalars(
        stmt.order_by(TestSession.created_at.desc(), TestSession.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(page), total or 0


async def get_version(db: AsyncSession, ts: TestSession) -> TestVersion:
    version = await db.get(TestVersion, ts.test_version_id)
    assert version is not None  # FK
    return version


async def get_answers(db: AsyncSession, ts: TestSession) -> dict[int, Any]:
    rows = await db.scalars(select(Answer).where(Answer.test_session_id == ts.id))
    return {a.question_number: a.value for a in rows}
