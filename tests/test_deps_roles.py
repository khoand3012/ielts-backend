import pytest

from api.deps import require_role
from core.exceptions import ForbiddenError
from core.models import Role, User


async def test_require_role_allows_listed_role() -> None:
    user = User(email="a@t.com", password_hash="x", role="teacher")
    assert await require_role(Role.TEACHER, Role.ADMIN)(user) is user


async def test_require_role_rejects_other_role() -> None:
    user = User(email="b@t.com", password_hash="x", role="student")
    with pytest.raises(ForbiddenError):
        await require_role(Role.ADMIN)(user)
