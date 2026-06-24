import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AuthError, ConflictError
from core.services import auth


async def test_register_then_login(db_session: AsyncSession) -> None:
    user = await auth.register(db_session, "x@y.com", "password123")
    assert user.email == "x@y.com"
    assert user.plan == "free"

    tokens = await auth.login(db_session, "x@y.com", "password123")
    assert tokens.access_token
    assert tokens.refresh_token


async def test_register_duplicate_email_conflicts(db_session: AsyncSession) -> None:
    await auth.register(db_session, "dup@y.com", "password123")
    with pytest.raises(ConflictError):
        await auth.register(db_session, "dup@y.com", "password123")


async def test_login_bad_password_raises(db_session: AsyncSession) -> None:
    await auth.register(db_session, "z@y.com", "password123")
    with pytest.raises(AuthError):
        await auth.login(db_session, "z@y.com", "wrongpass")


async def test_refresh_rotates_and_blocks_reuse(db_session: AsyncSession) -> None:
    await auth.register(db_session, "r@y.com", "password123")
    tokens = await auth.login(db_session, "r@y.com", "password123")

    new_tokens = await auth.refresh(db_session, tokens.refresh_token)
    assert new_tokens.refresh_token != tokens.refresh_token

    # reusing the old (now revoked) refresh token must fail
    with pytest.raises(AuthError):
        await auth.refresh(db_session, tokens.refresh_token)


async def test_logout_revokes_token(db_session: AsyncSession) -> None:
    await auth.register(db_session, "l@y.com", "password123")
    tokens = await auth.login(db_session, "l@y.com", "password123")
    await auth.logout(db_session, tokens.refresh_token)
    with pytest.raises(AuthError):
        await auth.refresh(db_session, tokens.refresh_token)
