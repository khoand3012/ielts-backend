import pytest

from core.exceptions import AuthError
from core.services.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_round_trip() -> None:
    h = hash_password("hunter2pw")
    assert h != "hunter2pw"
    assert verify_password("hunter2pw", h) is True
    assert verify_password("wrong", h) is False


def test_access_token_round_trip() -> None:
    token = create_access_token("user-123")
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_refresh_token_has_jti() -> None:
    token, jti, _expires_at = create_refresh_token("user-123")
    payload = decode_token(token)
    assert payload["jti"] == jti
    assert payload["type"] == "refresh"


def test_decode_rejects_garbage() -> None:
    with pytest.raises(AuthError):
        decode_token("not.a.jwt")
