from httpx import AsyncClient


async def test_register_returns_user(app_client: AsyncClient) -> None:
    resp = await app_client.post(
        "/auth/register", json={"email": "new@u.com", "password": "password123"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new@u.com"
    assert body["plan"] == "free"
    assert "id" in body


async def test_login_and_me(app_client: AsyncClient) -> None:
    await app_client.post("/auth/register", json={"email": "me@u.com", "password": "password123"})
    login = await app_client.post(
        "/auth/login", json={"email": "me@u.com", "password": "password123"}
    )
    assert login.status_code == 200
    access = login.json()["access_token"]

    me = await app_client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == "me@u.com"


async def test_me_requires_auth(app_client: AsyncClient) -> None:
    resp = await app_client.get("/auth/me")
    assert resp.status_code == 401
