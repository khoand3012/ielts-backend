"""A student's attempt at one test version: start, read, answers, submission."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import (
    ConflictError,
    NotFoundError,
    SessionClosedError,
    ValidationAppError,
)
from core.models import (
    Answer,
    GradingMethod,
    SessionMode,
    SessionStatus,
    Skill,
    SubmitReason,
    Test,
    TestSession,
    TestVersion,
    User,
)
from core.schemas.session import AnswerInput
from core.services import catalog, storage
from core.services.content import ItemSpec, item_index
from core.services.entitlements import check_and_consume
from core.settings import get_settings

_LIMITED_SKILLS = (Skill.WRITING, Skill.SPEAKING)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _grace() -> timedelta:
    return timedelta(seconds=get_settings().session_grace_seconds)


def _expired(ts: TestSession, now: datetime) -> bool:
    return ts.deadline is not None and now > ts.deadline + _grace()


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
    # Over the limit raises before anything is written.
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
    """The caller's session; an exam session past its deadline is submitted first."""
    now = _now(now)
    ts = await _owned(db, user, session_id)
    if ts.status == SessionStatus.IN_PROGRESS and _expired(ts, now):
        await _submit(db, TestSession.id == ts.id, SubmitReason.TIMEOUT, now)
        await db.refresh(ts)
    return ts


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


async def tests_for(db: AsyncSession, sessions: list[TestSession]) -> dict[uuid.UUID, Test]:
    """The test each session belongs to, keyed by session id, in one query."""
    version_ids = {ts.test_version_id for ts in sessions}
    rows = await db.execute(
        select(TestVersion.id, Test)
        .join(Test, Test.id == TestVersion.test_id)
        .where(TestVersion.id.in_(version_ids))
    )
    by_version = {version_id: test for version_id, test in rows.tuples()}
    return {ts.id: by_version[ts.test_version_id] for ts in sessions}


_ASSET_FIELDS = {"audio", "image"}


async def student_content(content: dict[str, Any]) -> dict[str, Any]:
    """A copy of a version's content with every asset key replaced by a signed GET URL.
    `content` holds only student-visible fields; answer keys and transcripts live elsewhere."""

    async def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if key in _ASSET_FIELDS and isinstance(value, str):
                    out[key] = await storage.signed_get_url(value)
                else:
                    out[key] = await walk(value)
            return out
        if isinstance(node, list):
            return [await walk(value) for value in node]
        return node

    signed: dict[str, Any] = await walk(content)
    return signed


async def get_answers(db: AsyncSession, ts: TestSession) -> dict[int, Any]:
    rows = await db.scalars(select(Answer).where(Answer.test_session_id == ts.id))
    return {a.question_number: a.value for a in rows}


async def _lock_open(
    db: AsyncSession, user: User, session_id: uuid.UUID, now: datetime
) -> TestSession:
    """The caller's session, row-locked for this transaction; it must still be open."""
    ts = await db.scalar(
        select(TestSession)
        .where(TestSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ts is None or ts.user_id != user.id:
        raise NotFoundError("session not found")
    if ts.status != SessionStatus.IN_PROGRESS:
        raise SessionClosedError("session is already submitted")
    if _expired(ts, now):
        # Submit it (committed) before refusing, so the refusal doesn't roll it back.
        await _submit(db, TestSession.id == ts.id, SubmitReason.TIMEOUT, now)
        raise SessionClosedError("time is up; the session has been submitted")
    return ts


def _invalid(question: int, message: str) -> ValidationAppError:
    return ValidationAppError(message, extra={"question": question})


def _check_value(spec: ItemSpec, value: str | list[str]) -> None:
    settings = get_settings()
    q = spec.key
    if spec.kind == "choice":
        if not isinstance(value, list):
            raise _invalid(q, f"question {q} takes a list of option keys")
        if not 1 <= len(value) <= spec.max_select or len(set(value)) != len(value):
            raise _invalid(q, f"question {q} takes 1 to {spec.max_select} different options")
        if not set(value) <= spec.option_keys:
            raise _invalid(q, f"question {q} has no such option")
    elif spec.kind == "single_choice":
        if not isinstance(value, str) or value not in spec.option_keys:
            raise _invalid(q, f"question {q} takes one option key")
    elif spec.kind in ("text", "essay"):
        limit = settings.max_text_answer_chars if spec.kind == "text" else settings.max_essay_chars
        if not isinstance(value, str) or len(value) > limit:
            raise _invalid(q, f"question {q} takes text of at most {limit} characters")
    else:
        raise _invalid(q, "recorded answers are not supported yet")


async def save_answers(
    db: AsyncSession,
    user: User,
    session_id: uuid.UUID,
    answers: list[AnswerInput],
    now: datetime | None = None,
) -> dict[int, Any]:
    """Validate every answer, then upsert them all (`None` clears one)."""
    now = _now(now)
    # Validate everything before locking; nothing is written unless all answers are valid.
    ts = await _owned(db, user, session_id)
    version = await get_version(db, ts)
    index = item_index(Skill(ts.skill), catalog.load_content(Skill(ts.skill), version))
    for answer in answers:
        spec = index.get(answer.question)
        if spec is None:
            raise _invalid(answer.question, f"there is no question {answer.question}")
        if answer.value is not None:
            _check_value(spec, answer.value)

    ts = await _lock_open(db, user, session_id, now)

    for answer in answers:
        if answer.value is None:
            await db.execute(
                delete(Answer).where(
                    Answer.test_session_id == ts.id, Answer.question_number == answer.question
                )
            )
        else:
            await db.execute(
                insert(Answer)
                .values(test_session_id=ts.id, question_number=answer.question, value=answer.value)
                .on_conflict_do_update(
                    index_elements=[Answer.test_session_id, Answer.question_number],
                    set_={"value": answer.value, "updated_at": now},
                )
            )
    await db.commit()
    return await get_answers(db, ts)


async def set_manual_grading(
    db: AsyncSession, user: User, session_id: uuid.UUID, value: bool, now: datetime | None = None
) -> TestSession:
    ts = await _lock_open(db, user, session_id, _now(now))
    if ts.skill != Skill.WRITING:
        raise ValidationAppError("only writing tests can request manual grading")
    ts.request_manual_grading = value
    await db.commit()
    await db.refresh(ts)
    return ts


async def on_session_submitted(db: AsyncSession, ts: TestSession) -> None:
    """Runs once per submitted session, inside the submitting transaction. Grading phases
    (auto-grading, AI, human) hook in here."""
    return None


_GRADING_METHOD = case(
    (TestSession.skill.in_([Skill.READING, Skill.LISTENING]), GradingMethod.AUTO.value),
    (TestSession.skill == Skill.SPEAKING, GradingMethod.HUMAN.value),
    (TestSession.request_manual_grading, GradingMethod.HUMAN.value),
    else_=GradingMethod.AI.value,
)


async def _submit(
    db: AsyncSession, which: ColumnElement[bool], reason: SubmitReason, now: datetime
) -> list[TestSession]:
    """Submit matching sessions that are still in progress, in one conditional UPDATE, so a
    session is submitted exactly once however many callers race. Commits."""
    stmt = (
        update(TestSession)
        .where(which, TestSession.status == SessionStatus.IN_PROGRESS)
        .values(
            status=SessionStatus.SUBMITTED,
            submitted_at=now,
            submit_reason=reason,
            grading_method=_GRADING_METHOD,
            updated_at=now,
        )
        .returning(TestSession)
        .execution_options(populate_existing=True)
    )
    submitted = list(await db.scalars(stmt))
    for ts in submitted:
        await on_session_submitted(db, ts)
    await db.commit()
    return submitted


async def submit_session(
    db: AsyncSession, user: User, session_id: uuid.UUID, now: datetime | None = None
) -> TestSession:
    now = _now(now)
    ts = await db.scalar(
        select(TestSession)
        .where(TestSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ts is None or ts.user_id != user.id:
        raise NotFoundError("session not found")
    if ts.status != SessionStatus.IN_PROGRESS:
        raise SessionClosedError("session is already submitted")
    reason = SubmitReason.TIMEOUT if _expired(ts, now) else SubmitReason.STUDENT
    [submitted] = await _submit(db, TestSession.id == ts.id, reason, now)
    return submitted


async def auto_submit_expired(db: AsyncSession, now: datetime | None = None) -> int:
    """Submit every exam session past its deadline plus grace. Returns how many."""
    now = _now(now)
    expired = and_(TestSession.deadline.is_not(None), TestSession.deadline < now - _grace())
    return len(await _submit(db, expired, SubmitReason.TIMEOUT, now))
