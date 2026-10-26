import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, DbSession
from core.models import GradingMethod, TestSession
from core.schemas.session import (
    AnswersRequest,
    AnswersResponse,
    ResultQuestion,
    SessionDetail,
    SessionListResponse,
    SessionRead,
    SessionResult,
    SessionStartRequest,
    SessionUpdateRequest,
)
from core.services import auto_grading, sessions

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _read(ts: TestSession, info: sessions.SessionTest) -> SessionRead:
    test = info.test
    return SessionRead(
        id=ts.id,
        test_id=test.id,
        test_version_id=ts.test_version_id,
        title=test.title,
        skill=ts.skill,
        mode=ts.mode,
        status=ts.status,
        started_at=ts.started_at,
        deadline=ts.deadline,
        submitted_at=ts.submitted_at,
        submit_reason=ts.submit_reason,
        request_manual_grading=ts.request_manual_grading,
        grading_method=ts.grading_method,
        module=info.module,
        raw_score=ts.raw_score,
        band=ts.band,
        server_time=datetime.now(UTC),
    )


async def _with_test(db: AsyncSession, ts: TestSession) -> SessionRead:
    return _read(ts, (await sessions.tests_for(db, [ts]))[ts.id])


async def _detail(db: AsyncSession, ts: TestSession) -> SessionDetail:
    version = await sessions.get_version(db, ts)
    return SessionDetail(
        **(await _with_test(db, ts)).model_dump(),
        content=await sessions.student_content(version.content),
        answers=await sessions.get_answers(db, ts),
    )


@router.post(
    "",
    response_model=SessionDetail,
    status_code=status.HTTP_201_CREATED,
    operation_id="start_session",
)
async def start_session(
    body: SessionStartRequest, user: CurrentUser, session: DbSession
) -> SessionDetail:
    ts = await sessions.start_session(session, user, body.test_id, body.mode)
    return await _detail(session, ts)


@router.get("", response_model=SessionListResponse, operation_id="list_sessions")
async def list_sessions(
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionListResponse:
    page, total = await sessions.list_sessions(session, user, limit=limit, offset=offset)
    tests = await sessions.tests_for(session, page)
    return SessionListResponse(items=[_read(ts, tests[ts.id]) for ts in page], total=total)


@router.get("/{session_id}", response_model=SessionDetail, operation_id="get_session")
async def get_session(
    session_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> SessionDetail:
    return await _detail(session, await sessions.get_session(session, user, session_id))


@router.put("/{session_id}/answers", response_model=AnswersResponse, operation_id="save_answers")
async def save_answers(
    session_id: uuid.UUID, body: AnswersRequest, user: CurrentUser, session: DbSession
) -> AnswersResponse:
    saved = await sessions.save_answers(session, user, session_id, body.answers)
    return AnswersResponse(answers=saved)


@router.patch("/{session_id}", response_model=SessionRead, operation_id="update_session")
async def update_session(
    session_id: uuid.UUID, body: SessionUpdateRequest, user: CurrentUser, session: DbSession
) -> SessionRead:
    ts = await sessions.set_manual_grading(session, user, session_id, body.request_manual_grading)
    return await _with_test(session, ts)


@router.post("/{session_id}/submit", response_model=SessionRead, operation_id="submit_session")
async def submit_session(
    session_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> SessionRead:
    return await _with_test(session, await sessions.submit_session(session, user, session_id))


def _question(item: auto_grading.MarkedItem) -> ResultQuestion:
    return ResultQuestion(
        number=item.spec.key,
        numbers=list(item.spec.numbers),
        kind=item.spec.kind,
        answer=item.answer,
        accepted=item.accepted,
        correct_choices=item.correct_choices,
        marks=item.marks,
        max_marks=item.max_marks,
    )


def _result(
    ts: TestSession, info: sessions.SessionTest, score: auto_grading.Score | None
) -> SessionResult:
    graded = ts.grading_method == GradingMethod.AUTO and ts.raw_score is not None
    return SessionResult(
        session_id=ts.id,
        test_id=info.test.id,
        title=info.test.title,
        skill=ts.skill,
        module=info.module,
        mode=ts.mode,
        submit_reason=ts.submit_reason,
        grading_method=ts.grading_method,
        started_at=ts.started_at,
        submitted_at=ts.submitted_at,
        time_spent_seconds=sessions.time_spent_seconds(ts),
        status="graded" if graded else "being_graded",
        # the stored total, so the result always matches the session list
        raw_score=ts.raw_score if graded else None,
        max_score=score.max_score if score else None,
        band=ts.band if graded else None,
        answered=score.answered if score else None,
        questions=[_question(i) for i in score.items] if score else None,
    )


@router.get("/{session_id}/result", response_model=SessionResult, operation_id="get_session_result")
async def get_session_result(
    session_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> SessionResult:
    ts, score = await sessions.get_result(session, user, session_id)
    return _result(ts, (await sessions.tests_for(session, [ts]))[ts.id], score)
