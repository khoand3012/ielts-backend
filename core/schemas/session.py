import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from core.models import GradingMethod, SessionMode, SessionStatus, Skill, SubmitReason


class AnswerInput(BaseModel):
    question: int
    # option keys for multiple choice, one key for matching, text otherwise; null clears
    value: str | list[str] | None


class SessionStartRequest(BaseModel):
    test_id: uuid.UUID
    mode: SessionMode


class SessionUpdateRequest(BaseModel):
    request_manual_grading: bool


class AnswersRequest(BaseModel):
    answers: list[AnswerInput] = Field(min_length=1, max_length=100)


class AnswersResponse(BaseModel):
    answers: dict[int, Any]


class SessionRead(BaseModel):
    id: uuid.UUID
    test_id: uuid.UUID
    test_version_id: uuid.UUID
    title: str
    skill: Skill
    mode: SessionMode
    status: SessionStatus
    started_at: datetime
    deadline: datetime | None
    submitted_at: datetime | None
    submit_reason: SubmitReason | None
    request_manual_grading: bool
    grading_method: GradingMethod | None
    server_time: datetime


class SessionDetail(SessionRead):
    # student-visible content only, with asset keys replaced by signed URLs
    content: dict[str, Any]
    answers: dict[int, Any]


class SessionListResponse(BaseModel):
    items: list[SessionRead]
    total: int
