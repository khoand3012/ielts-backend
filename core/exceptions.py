from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, extra: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class SessionClosedError(ConflictError):
    """The session is submitted (or past its deadline), so it can't change any more."""

    code = "session_closed"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class QuotaExceededError(AppError):
    """Daily Writing/Speaking limit reached; `extra["resets_at"]` says when it resets."""

    status_code = 429
    code = "quota_exceeded"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


class ContentProblemsError(ValidationAppError):
    """A test version can't be published; `extra["problems"]` lists why."""

    code = "content_problems"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "extra": exc.extra}},
        )
