from fastapi import APIRouter, status

from api.deps import CurrentUser, DbSession
from core.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from core.schemas.user import UserRead
from core.services import auth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="auth_register",
)
async def register(body: RegisterRequest, session: DbSession) -> UserRead:
    user = await auth.register(session, body.email, body.password)
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenResponse, operation_id="auth_login")
async def login(body: LoginRequest, session: DbSession) -> TokenResponse:
    return await auth.login(session, body.email, body.password)


@router.post("/refresh", response_model=TokenResponse, operation_id="auth_refresh")
async def refresh(body: RefreshRequest, session: DbSession) -> TokenResponse:
    return await auth.refresh(session, body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="auth_logout")
async def logout(body: RefreshRequest, session: DbSession) -> None:
    await auth.logout(session, body.refresh_token)


@router.get("/me", response_model=UserRead, operation_id="auth_me")
async def me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)
