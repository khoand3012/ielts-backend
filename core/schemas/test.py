import uuid

from pydantic import BaseModel

from core.models import Skill


class TestSummary(BaseModel):
    __test__ = False  # not a pytest test class

    id: uuid.UUID
    skill: Skill
    title: str
    exam_time_limit_minutes: int


class TestListResponse(BaseModel):
    __test__ = False

    items: list[TestSummary]
    total: int
