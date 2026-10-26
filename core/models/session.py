import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from core.models.enums import (
    GradingMethod,
    SessionMode,
    SessionStatus,
    Skill,
    SubmitReason,
    check_in,
)


class TestSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "test_sessions"
    __test__ = False
    __table_args__ = (
        CheckConstraint(check_in("skill", Skill), name="skill"),
        CheckConstraint(check_in("mode", SessionMode), name="mode"),
        CheckConstraint(check_in("status", SessionStatus), name="status"),
        CheckConstraint(check_in("submit_reason", SubmitReason), name="submit_reason"),
        CheckConstraint(check_in("grading_method", GradingMethod), name="grading_method"),
        CheckConstraint("raw_score BETWEEN 0 AND 40", name="raw_score"),
        Index("ix_test_sessions_status_deadline", "status", "deadline"),
        Index("ix_test_sessions_user_id_created_at", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    test_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_versions.id"))
    skill: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=SessionStatus.IN_PROGRESS)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    submit_reason: Mapped[str | None] = mapped_column(String(16), default=None)
    request_manual_grading: Mapped[bool] = mapped_column(default=False)
    grading_method: Mapped[str | None] = mapped_column(String(16), default=None)
    # Reading/Listening: set when scored. A partial practice attempt has a mark but no band.
    raw_score: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    band: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), default=None)


class Answer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint(
            "test_session_id", "question_number", name="uq_answers_test_session_id_question_number"
        ),
    )

    test_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_sessions.id", ondelete="CASCADE")
    )
    question_number: Mapped[int]
    value: Mapped[Any] = mapped_column(JSONB)
