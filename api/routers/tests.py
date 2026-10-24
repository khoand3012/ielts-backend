import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from api.deps import CurrentUser, DbSession
from core.models import Skill, Test
from core.schemas.test import TestListResponse, TestSummary
from core.services import catalog
from core.services.sessions import exam_time_limit

router = APIRouter(prefix="/tests", tags=["tests"])


def _summary(test: Test) -> TestSummary:
    skill = Skill(test.skill)
    return TestSummary(
        id=test.id,
        skill=skill,
        title=test.title,
        exam_time_limit_minutes=int(exam_time_limit(skill).total_seconds() // 60),
    )


@router.get("", response_model=TestListResponse, operation_id="list_tests")
async def list_tests(
    _: CurrentUser,
    session: DbSession,
    skill: Skill | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TestListResponse:
    page, total = await catalog.list_published(session, skill=skill, limit=limit, offset=offset)
    return TestListResponse(items=[_summary(t) for t in page], total=total)


@router.get("/{test_id}", response_model=TestSummary, operation_id="get_test")
async def get_test(test_id: uuid.UUID, _: CurrentUser, session: DbSession) -> TestSummary:
    return _summary(await catalog.get_published_test(session, test_id))
