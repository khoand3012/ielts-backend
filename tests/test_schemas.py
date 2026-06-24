import pytest
from pydantic import ValidationError

from core.schemas.auth import RegisterRequest, TokenResponse


def test_register_requires_valid_email() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="not-an-email", password="longenough")


def test_register_password_min_length() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="short")


def test_token_response_defaults_bearer() -> None:
    t = TokenResponse(access_token="a", refresh_token="r")
    assert t.token_type == "bearer"
