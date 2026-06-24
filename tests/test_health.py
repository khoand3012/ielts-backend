from httpx import AsyncClient


async def test_health_ok(app_client: AsyncClient) -> None:
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
