from collections.abc import Awaitable, Callable

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from core.models import Module, Skill, Test, User
from tests.helpers import publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_list_and_get_published_tests(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    v = await publish_test(db_session, Skill.LISTENING)

    resp = await app_client.get(
        "/tests", params={"skill": "listening", "limit": 100}, headers=h(tok)
    )
    assert resp.status_code == 200
    items = {i["id"]: i for i in resp.json()["items"]}
    item = items[str(v.test_id)]
    assert item == {
        "id": str(v.test_id),
        "skill": "listening",
        "title": "listening test",
        "exam_time_limit_minutes": 32,
        "module": None,
    }

    resp = await app_client.get(f"/tests/{v.test_id}", headers=h(tok))
    assert resp.status_code == 200
    assert resp.json() == item


async def test_draft_test_is_404(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    test = Test(skill="reading", slug=f"draft-{uuid7()}", title="T")
    db_session.add(test)
    await db_session.commit()
    resp = await app_client.get(f"/tests/{test.id}", headers=h(tok))
    assert resp.status_code == 404


async def test_tests_require_auth(app_client: AsyncClient) -> None:
    assert (await app_client.get("/tests")).status_code == 401


async def test_list_tests_module_filter(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    academic = await publish_test(db_session, Skill.READING)
    general = await publish_test(db_session, Skill.READING, module=Module.GENERAL_TRAINING)
    resp = await app_client.get(
        "/tests",
        params={"skill": "reading", "module": "general_training", "limit": 100},
        headers=h(tok),
    )
    assert resp.status_code == 200
    items = {i["id"]: i for i in resp.json()["items"]}
    assert str(general.test_id) in items
    assert str(academic.test_id) not in items
    assert {i["module"] for i in items.values()} == {"general_training"}

    one = await app_client.get(f"/tests/{academic.test_id}", headers=h(tok))
    assert one.json()["module"] == "academic"
