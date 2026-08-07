from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.exceptions import AuthError, NotFoundError, register_exception_handlers


def test_app_error_attributes() -> None:
    err = NotFoundError("user missing")
    assert err.status_code == 404
    assert err.code == "not_found"
    assert err.message == "user missing"


async def test_handler_maps_error_to_json() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise AuthError("nope")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom")
    assert resp.status_code == 401
    assert resp.json() == {"error": {"code": "unauthorized", "message": "nope", "extra": {}}}
