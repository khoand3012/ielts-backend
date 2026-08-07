# tests/test_auth_flow_e2e.py
from httpx import AsyncClient


async def test_full_auth_lifecycle(app_client: AsyncClient) -> None:
    # register
    reg = await app_client.post(
        "/auth/register", json={"email": "flow@u.com", "password": "password123"}
    )
    assert reg.status_code == 201

    # login
    login = await app_client.post(
        "/auth/login", json={"email": "flow@u.com", "password": "password123"}
    )
    assert login.status_code == 200
    tokens = login.json()

    # access protected route
    me = await app_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200

    # refresh -> new pair
    refreshed = await app_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # new access token works
    me2 = await app_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {new_tokens['access_token']}"}
    )
    assert me2.status_code == 200

    # old refresh token now rejected (rotation)
    reused = await app_client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reused.status_code == 401

    # logout revokes current refresh token
    logout = await app_client.post(
        "/auth/logout", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert logout.status_code == 204

    after_logout = await app_client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert after_logout.status_code == 401
