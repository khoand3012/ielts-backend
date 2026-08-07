import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt

from core.exceptions import AuthError
from core.settings import get_settings

_settings = get_settings()
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(subject: str) -> str:
    expire = _now() + timedelta(seconds=_settings.access_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    return cast(str, jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm))


def create_refresh_token(subject: str) -> tuple[str, str, datetime]:
    jti = str(uuid.uuid4())
    expire = _now() + timedelta(seconds=_settings.refresh_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "refresh",
        "exp": expire,
        "jti": jti,
    }
    token = cast(str, jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm))
    return token, jti, expire


def decode_token(token: str) -> dict[str, Any]:
    try:
        return cast(
            dict[str, Any],
            jwt.decode(token, _settings.jwt_secret, algorithms=[_settings.jwt_algorithm]),
        )
    except JWTError as exc:
        raise AuthError("invalid or expired token") from exc
