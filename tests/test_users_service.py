import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from core.exceptions import ForbiddenError, NotFoundError
from core.models import Plan, Role, User
from core.services import auth, users


async def _user(session: AsyncSession, email: str | None = None, role: Role = Role.STUDENT) -> User:
    user = await auth.register(session, email or f"u-{uuid7()}@t.com", "password123")
    user.role = role
    await session.commit()
    return user


async def test_update_user_changes_role_and_plan(db_session: AsyncSession) -> None:
    admin = await _user(db_session, role=Role.ADMIN)
    target = await _user(db_session)
    updated = await users.update_user(
        db_session, actor=admin, user_id=target.id, role=Role.TEACHER, plan=Plan.PAID
    )
    assert updated.role == "teacher"
    assert updated.plan == "paid"
    reloaded = await db_session.get(User, target.id)
    assert reloaded is not None
    await db_session.refresh(reloaded)
    assert (reloaded.role, reloaded.plan) == ("teacher", "paid")


async def test_update_user_missing_raises_not_found(db_session: AsyncSession) -> None:
    admin = await _user(db_session, role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        await users.update_user(db_session, actor=admin, user_id=uuid7(), role=Role.TEACHER)


async def test_admin_cannot_demote_self(db_session: AsyncSession) -> None:
    admin = await _user(db_session, role=Role.ADMIN)
    with pytest.raises(ForbiddenError):
        await users.update_user(db_session, actor=admin, user_id=admin.id, role=Role.TEACHER)
    await db_session.refresh(admin)
    assert admin.role == "admin"


async def test_admin_can_change_own_plan(db_session: AsyncSession) -> None:
    admin = await _user(db_session, role=Role.ADMIN)
    updated = await users.update_user(db_session, actor=admin, user_id=admin.id, plan=Plan.PAID)
    assert updated.plan == "paid"
    assert updated.role == "admin"


async def test_list_users_filters_by_query_and_role(db_session: AsyncSession) -> None:
    token = f"lf{uuid7().hex[:12]}"
    await _user(db_session, f"{token}-a@t.com")
    teacher = await _user(db_session, f"{token}-b@t.com", role=Role.TEACHER)

    page, total = await users.list_users(db_session, q=token.upper())
    assert total == 2
    assert len(page) == 2

    page, total = await users.list_users(db_session, q=token, role=Role.TEACHER)
    assert total == 1
    assert [u.id for u in page] == [teacher.id]


async def test_list_users_paginates_newest_first(db_session: AsyncSession) -> None:
    token = f"pg{uuid7().hex[:12]}"
    first = await _user(db_session, f"{token}-1@t.com")
    second = await _user(db_session, f"{token}-2@t.com")
    page, total = await users.list_users(db_session, q=token, limit=1)
    assert total == 2
    assert [u.id for u in page] == [second.id]
    page, _ = await users.list_users(db_session, q=token, limit=1, offset=1)
    assert [u.id for u in page] == [first.id]


async def test_list_users_search_is_literal(db_session: AsyncSession) -> None:
    token = f"lt{uuid7().hex[:12]}"
    underscored = await _user(db_session, f"{token}_a@t.com")
    await _user(db_session, f"{token}xa@t.com")

    page, total = await users.list_users(db_session, q=f"{token}_")
    assert total == 1
    assert [u.id for u in page] == [underscored.id]

    _, total = await users.list_users(db_session, q=f"%{token}")
    assert total == 0


async def test_set_role_by_email(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    updated = await users.set_role_by_email(db_session, user.email, Role.ADMIN)
    assert updated.role == "admin"
    with pytest.raises(NotFoundError):
        await users.set_role_by_email(db_session, f"nobody-{uuid7()}@t.com", Role.ADMIN)
