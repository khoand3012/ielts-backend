import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import async_session_factory
from core.exceptions import AuthError, ForbiddenError, NotFoundError
from core.models import Role, User
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
    try:
        return await get_user_by_id(session, uuid.UUID(str(payload["sub"])))
    except NotFoundError:
        raise AuthError("invalid credentials") from None


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: Role) -> Callable[[User], Awaitable[User]]:
    async def _require_role(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ForbiddenError("insufficient role")
        return user

    return _require_role


AdminUser = Annotated[User, Depends(require_role(Role.ADMIN))]
TeacherUser = Annotated[User, Depends(require_role(Role.TEACHER, Role.ADMIN))]
