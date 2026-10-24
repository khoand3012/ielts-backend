import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, EmailStr, model_validator

from core.models import Plan, Role


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    plan: str
    role: str
    created_at: datetime


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int


class UserUpdateRequest(BaseModel):
    role: Role | None = None
    plan: Plan | None = None

    @model_validator(mode="after")
    def _require_a_change(self) -> Self:
        if self.role is None and self.plan is None:
            raise ValueError("nothing to update")
        return self
