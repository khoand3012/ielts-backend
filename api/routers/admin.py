import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from api.deps import AdminUser, DbSession
from core.models import Role
from core.schemas.user import UserListResponse, UserRead, UserUpdateRequest
from core.services import users

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=UserListResponse, operation_id="admin_list_users")
async def list_users(
    _: AdminUser,
    session: DbSession,
    q: Annotated[str | None, Query(max_length=320)] = None,
    role: Role | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserListResponse:
    page, total = await users.list_users(session, q=q, role=role, limit=limit, offset=offset)
    return UserListResponse(items=[UserRead.model_validate(u) for u in page], total=total)


@router.patch("/users/{user_id}", response_model=UserRead, operation_id="admin_update_user")
async def update_user(
    user_id: uuid.UUID, body: UserUpdateRequest, admin: AdminUser, session: DbSession
) -> UserRead:
    user = await users.update_user(
        session, actor=admin, user_id=user_id, role=body.role, plan=body.plan
    )
    return UserRead.model_validate(user)
