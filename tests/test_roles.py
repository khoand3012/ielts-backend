import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from core.models import Role, User
from core.services import auth


async def test_new_user_is_student(db_session: AsyncSession) -> None:
    user = await auth.register(db_session, f"s-{uuid7()}@t.com", "password123")
    assert user.role == Role.STUDENT


async def test_role_check_constraint(db_session: AsyncSession) -> None:
    db_session.add(User(email=f"bad-{uuid7()}@t.com", password_hash="x", role="superuser"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_register_returns_role(app_client: AsyncClient) -> None:
    resp = await app_client.post(
        "/auth/register", json={"email": f"r-{uuid7()}@t.com", "password": "password123"}
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "student"


async def test_register_ignores_role_field(app_client: AsyncClient) -> None:
    resp = await app_client.post(
        "/auth/register",
        json={"email": f"x-{uuid7()}@t.com", "password": "password123", "role": "admin"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "student"
