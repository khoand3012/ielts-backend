import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import async_session_factory
from core.exceptions import AuthError
from core.models import User
from core.services.auth import get_user_by_id
from core.services.security import decode_token

_bearer = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    session: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None:
        raise AuthError("missing bearer token")
    payload = decode_token(creds.credentials)
    if payload.get("type") != "access":
        raise AuthError("not an access token")
    return await get_user_by_id(session, uuid.UUID(str(payload["sub"])))


CurrentUser = Annotated[User, Depends(get_current_user)]
