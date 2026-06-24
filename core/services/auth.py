import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AuthError, ConflictError, NotFoundError
from core.models import RefreshToken, User
from core.schemas.auth import TokenResponse
from core.services.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


async def register(session: AsyncSession, email: str, password: str) -> User:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise ConflictError("email already registered")
    user = User(email=email, password_hash=hash_password(password), plan="free")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _issue_tokens(session: AsyncSession, user: User) -> TokenResponse:
    access = create_access_token(str(user.id))
    refresh_token, jti, expires_at = create_refresh_token(str(user.id))
    session.add(RefreshToken(user_id=user.id, jti=jti, expires_at=expires_at))
    await session.commit()
    return TokenResponse(access_token=access, refresh_token=refresh_token)


async def login(session: AsyncSession, email: str, password: str) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("invalid credentials")
    return await _issue_tokens(session, user)


async def refresh(session: AsyncSession, refresh_token: str) -> TokenResponse:
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise AuthError("not a refresh token")
    jti = str(payload["jti"])
    row = await session.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if row is None or row.revoked_at is not None:
        raise AuthError("refresh token revoked or unknown")
    if row.expires_at <= datetime.now(UTC):
        raise AuthError("refresh token expired")

    row.revoked_at = datetime.now(UTC)  # rotation: revoke on use
    user = await get_user_by_id(session, row.user_id)
    return await _issue_tokens(session, user)


async def logout(session: AsyncSession, refresh_token: str) -> None:
    try:
        payload = decode_token(refresh_token)
    except AuthError:
        return  # idempotent: nothing to revoke
    jti = str(payload.get("jti", ""))
    row = await session.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("user not found")
    return user
