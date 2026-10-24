from collections.abc import Awaitable, Callable

from httpx import AsyncClient
from uuid_utils.compat import uuid7

from core.models import Role, User

MakeUser = Callable[..., Awaitable[tuple[User, str]]]


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_admin_lists_users(app_client: AsyncClient, make_user: MakeUser) -> None:
    _, tok = await make_user(Role.ADMIN)
    target, _ = await make_user()
    resp = await app_client.get("/admin/users", params={"q": target.email}, headers=h(tok))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == target.email
    assert body["items"][0]["role"] == "student"


async def test_admin_lists_users_by_role(app_client: AsyncClient, make_user: MakeUser) -> None:
    _, tok = await make_user(Role.ADMIN)
    teacher, _ = await make_user(Role.TEACHER)
    resp = await app_client.get(
        "/admin/users", params={"q": teacher.email, "role": "teacher"}, headers=h(tok)
    )
    assert resp.json()["total"] == 1
    resp = await app_client.get(
        "/admin/users", params={"q": teacher.email, "role": "student"}, headers=h(tok)
    )
    assert resp.json()["total"] == 0


async def test_non_admin_forbidden(app_client: AsyncClient, make_user: MakeUser) -> None:
    for role in (Role.STUDENT, Role.TEACHER):
        _, tok = await make_user(role)
        assert (await app_client.get("/admin/users", headers=h(tok))).status_code == 403


async def test_admin_users_requires_auth(app_client: AsyncClient) -> None:
    assert (await app_client.get("/admin/users")).status_code == 401


async def test_admin_promotes_user(app_client: AsyncClient, make_user: MakeUser) -> None:
    _, tok = await make_user(Role.ADMIN)
    target, _ = await make_user()
    resp = await app_client.patch(
        f"/admin/users/{target.id}", json={"role": "teacher"}, headers=h(tok)
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "teacher"


async def test_admin_changes_plan(app_client: AsyncClient, make_user: MakeUser) -> None:
    _, tok = await make_user(Role.ADMIN)
    target, _ = await make_user()
    resp = await app_client.patch(
        f"/admin/users/{target.id}", json={"plan": "paid"}, headers=h(tok)
    )
    assert resp.status_code == 200
    assert resp.json()["plan"] == "paid"


async def test_role_change_applies_to_existing_token(
    app_client: AsyncClient, make_user: MakeUser
) -> None:
    _, admin_tok = await make_user(Role.ADMIN)
    user, user_tok = await make_user()
    assert (await app_client.get("/admin/users", headers=h(user_tok))).status_code == 403

    await app_client.patch(f"/admin/users/{user.id}", json={"role": "admin"}, headers=h(admin_tok))
    assert (await app_client.get("/admin/users", headers=h(user_tok))).status_code == 200

    await app_client.patch(
        f"/admin/users/{user.id}", json={"role": "student"}, headers=h(admin_tok)
    )
    assert (await app_client.get("/admin/users", headers=h(user_tok))).status_code == 403


async def test_admin_cannot_demote_self_via_api(
    app_client: AsyncClient, make_user: MakeUser
) -> None:
    admin, tok = await make_user(Role.ADMIN)
    resp = await app_client.patch(
        f"/admin/users/{admin.id}", json={"role": "student"}, headers=h(tok)
    )
    assert resp.status_code == 403


async def test_update_user_invalid_role_422(app_client: AsyncClient, make_user: MakeUser) -> None:
    _, tok = await make_user(Role.ADMIN)
    target, _ = await make_user()
    resp = await app_client.patch(
        f"/admin/users/{target.id}", json={"role": "superuser"}, headers=h(tok)
    )
    assert resp.status_code == 422


async def test_update_user_empty_body_422(app_client: AsyncClient, make_user: MakeUser) -> None:
    _, tok = await make_user(Role.ADMIN)
    target, _ = await make_user()
    resp = await app_client.patch(f"/admin/users/{target.id}", json={}, headers=h(tok))
    assert resp.status_code == 422


async def test_update_unknown_user_404(app_client: AsyncClient, make_user: MakeUser) -> None:
    _, tok = await make_user(Role.ADMIN)
    resp = await app_client.patch(
        f"/admin/users/{uuid7()}", json={"role": "teacher"}, headers=h(tok)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_list_users_limit_over_100_422(app_client: AsyncClient, make_user: MakeUser) -> None:
    _, tok = await make_user(Role.ADMIN)
    resp = await app_client.get("/admin/users", params={"limit": 101}, headers=h(tok))
    assert resp.status_code == 422
