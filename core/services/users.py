import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ForbiddenError, NotFoundError
from core.models import Plan, Role, User
from core.services.auth import get_user_by_id


async def list_users(
    session: AsyncSession,
    *,
    q: str | None = None,
    role: Role | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[User], int]:
    stmt = select(User)
    if q:
        stmt = stmt.where(User.email.icontains(q, autoescape=True))
    if role is not None:
        stmt = stmt.where(User.role == role)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    page = await session.scalars(
        stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset)
    )
    return list(page), total or 0


async def update_user(
    session: AsyncSession,
    *,
    actor: User,
    user_id: uuid.UUID,
    role: Role | None = None,
    plan: Plan | None = None,
) -> User:
    user = await get_user_by_id(session, user_id)
    if actor.id == user_id and role is not None and role != Role.ADMIN:
        raise ForbiddenError("admins cannot change their own role")
    if role is not None:
        user.role = role
    if plan is not None:
        user.plan = plan
    await session.commit()
    await session.refresh(user)
    return user


async def set_role_by_email(session: AsyncSession, email: str, role: Role) -> User:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise NotFoundError("user not found")
    user.role = role
    await session.commit()
    await session.refresh(user)
    return user
