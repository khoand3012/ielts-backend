import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from core.models.enums import Skill, VersionStatus, check_in


class Test(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tests"
    __test__ = False  # not a pytest test class
    __table_args__ = (CheckConstraint(check_in("skill", Skill), name="skill"),)

    skill: Mapped[str] = mapped_column(String(16))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_versions.id", ondelete="SET NULL", use_alter=True), default=None
    )


class TestVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "test_versions"
    __test__ = False
    __table_args__ = (
        CheckConstraint(check_in("status", VersionStatus), name="status"),
        UniqueConstraint("test_id", "version", name="uq_test_versions_test_id_version"),
        Index(
            "uq_test_versions_one_draft",
            "test_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    test_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"))
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), default=VersionStatus.DRAFT)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    answer_key: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    staff_content: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
