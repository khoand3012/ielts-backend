import uuid

from core.models import RefreshToken, User


def test_user_defaults() -> None:
    user = User(email="a@b.com", password_hash="x")
    assert user.plan == "free" or user.plan is None  # default applied at flush


def test_refresh_token_fields() -> None:
    rt = RefreshToken(user_id=uuid.uuid4(), jti="abc", expires_at=None)
    assert rt.jti == "abc"
    assert rt.revoked_at is None
