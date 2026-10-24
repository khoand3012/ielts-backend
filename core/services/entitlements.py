"""Daily limits on Writing and Speaking tests, used up when a test starts."""

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import QuotaExceededError
from core.models import DailyUsage, Plan, Skill, User
from core.settings import get_settings


def daily_limit(user: User, skill: Skill) -> int:
    settings = get_settings()
    limits = {
        (Plan.FREE, Skill.WRITING): settings.free_writing_daily,
        (Plan.FREE, Skill.SPEAKING): settings.free_speaking_daily,
        (Plan.PAID, Skill.WRITING): settings.paid_writing_daily,
        (Plan.PAID, Skill.SPEAKING): settings.paid_speaking_daily,
    }
    try:
        return limits[(Plan(user.plan), skill)]
    except KeyError:
        raise ValueError(f"{skill} has no daily limit") from None


async def check_and_consume(db: AsyncSession, user: User, skill: Skill, now: datetime) -> None:
    """Use up one of today's tests, or raise QuotaExceededError. Does not commit: it runs in
    the caller's transaction, so a start that fails later uses nothing."""
    today = now.astimezone(UTC).date()
    resets_at = datetime.combine(today + timedelta(days=1), time(), tzinfo=UTC)
    limit = daily_limit(user, skill)
    if limit > 0:
        # Insert or increment only while under the limit; the row lock makes two concurrent
        # starts take the last slot once.
        stmt = (
            insert(DailyUsage)
            .values(user_id=user.id, skill=skill, day=today, count=1)
            .on_conflict_do_update(
                index_elements=[DailyUsage.user_id, DailyUsage.skill, DailyUsage.day],
                set_={"count": DailyUsage.count + 1, "updated_at": now},
                where=DailyUsage.count < limit,
            )
            .returning(DailyUsage.count)
        )
        if await db.scalar(stmt) is not None:
            return
    raise QuotaExceededError(
        f"daily {skill} limit reached", extra={"resets_at": resets_at.isoformat()}
    )
