import uuid
from datetime import date

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, TimestampMixin
from core.models.enums import Skill, check_in


class DailyUsage(TimestampMixin, Base):
    """Writing/Speaking tests started per user per UTC day. The natural key is the PK."""

    __tablename__ = "daily_usage"
    __table_args__ = (CheckConstraint(check_in("skill", Skill), name="skill"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    skill: Mapped[str] = mapped_column(String(16), primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
