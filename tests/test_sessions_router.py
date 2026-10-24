import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Skill, User
from tests.helpers import publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
SECRET_KEYS = {"answer_key", "staff_content", "transcripts"}


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def fake_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def signed(key: str) -> str:
        return f"https://signed/{key}"

    monkeypatch.setattr("core.services.storage.signed_get_url", signed)


def assert_no_secrets(body: Any) -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            assert not SECRET_KEYS & set(node), node.keys()
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(body)
    text = json.dumps(body)
    assert "Secret transcript line" not in text
    assert "word8" not in text  # a Reading answer
    assert "alt31" not in text  # a Listening answer


async def start(
    client: AsyncClient, db: AsyncSession, tok: str, skill: Skill, mode: str = "exam"
) -> dict[str, Any]:
    v = await publish_test(db, skill)
    resp = await client.post(
        "/sessions", json={"test_id": str(v.test_id), "mode": mode}, headers=h(tok)
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_start_returns_content_without_answer_key(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    for skill in (Skill.READING, Skill.LISTENING):
        body = await start(app_client, db_session, tok, skill)
        assert body["status"] == "in_progress"
        assert body["content"]
        assert_no_secrets(body)

        sid = body["id"]
        assert_no_secrets((await app_client.get(f"/sessions/{sid}", headers=h(tok))).json())
        assert_no_secrets(
            (await app_client.get(f"/tests/{body['test_id']}", headers=h(tok))).json()
        )
        await app_client.post(f"/sessions/{sid}/submit", headers=h(tok))
        after = (await app_client.get(f"/sessions/{sid}", headers=h(tok))).json()
        assert after["status"] == "submitted"
        assert_no_secrets(after)


async def test_assets_are_signed_urls(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    writing = await start(app_client, db_session, tok, Skill.WRITING)
    assert writing["content"]["tasks"][0]["image"] == "https://signed/tests/f/task1.svg"
    listening = await start(app_client, db_session, tok, Skill.LISTENING)
    assert listening["content"]["parts"][3]["audio"] == "https://signed/tests/f/part4.mp3"


async def test_resume_returns_saved_answers(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    body = await start(app_client, db_session, tok, Skill.LISTENING)
    sid = body["id"]
    resp = await app_client.put(
        f"/sessions/{sid}/answers",
        json={"answers": [{"question": 21, "value": ["B", "D"]}, {"question": 1, "value": "x"}]},
        headers=h(tok),
    )
    assert resp.status_code == 200
    assert resp.json() == {"answers": {"1": "x", "21": ["B", "D"]}}

    resumed = (await app_client.get(f"/sessions/{sid}", headers=h(tok))).json()
    assert resumed["answers"] == {"1": "x", "21": ["B", "D"]}
    assert resumed["deadline"] is not None
    assert resumed["server_time"]


async def test_invalid_answer_is_422(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    body = await start(app_client, db_session, tok, Skill.LISTENING)
    resp = await app_client.put(
        f"/sessions/{body['id']}/answers",
        json={"answers": [{"question": 22, "value": ["B"]}]},
        headers=h(tok),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["extra"] == {"question": 22}


async def test_quota_exceeded_is_429_with_resets_at(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    v = await publish_test(db_session, Skill.WRITING)
    payload = {"test_id": str(v.test_id), "mode": "practice"}
    for _ in range(3):
        assert (await app_client.post("/sessions", json=payload, headers=h(tok))).status_code == 201
    resp = await app_client.post("/sessions", json=payload, headers=h(tok))
    assert resp.status_code == 429
    error = resp.json()["error"]
    assert error["code"] == "quota_exceeded"
    assert error["extra"]["resets_at"].endswith("T00:00:00+00:00")


async def test_manual_grading_toggle(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    body = await start(app_client, db_session, tok, Skill.WRITING)
    resp = await app_client.patch(
        f"/sessions/{body['id']}", json={"request_manual_grading": True}, headers=h(tok)
    )
    assert resp.status_code == 200
    assert resp.json()["request_manual_grading"] is True
    done = (await app_client.post(f"/sessions/{body['id']}/submit", headers=h(tok))).json()
    assert done["grading_method"] == "human"


async def test_list_my_sessions(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    first = await start(app_client, db_session, tok, Skill.READING)
    second = await start(app_client, db_session, tok, Skill.READING, "practice")
    body = (await app_client.get("/sessions", headers=h(tok))).json()
    assert body["total"] == 2
    assert [s["id"] for s in body["items"]] == [second["id"], first["id"]]
    assert "content" not in body["items"][0]


async def test_session_endpoints_require_auth(app_client: AsyncClient) -> None:
    assert (await app_client.get("/sessions")).status_code == 401
    assert (await app_client.post("/sessions", json={})).status_code == 401


async def test_other_users_session_404(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, owner = await make_user()
    _, other = await make_user()
    body = await start(app_client, db_session, owner, Skill.READING)
    assert (await app_client.get(f"/sessions/{body['id']}", headers=h(other))).status_code == 404


async def test_submit_then_save_is_409(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    body = await start(app_client, db_session, tok, Skill.READING)
    sid = body["id"]
    assert (await app_client.post(f"/sessions/{sid}/submit", headers=h(tok))).status_code == 200
    resp = await app_client.put(
        f"/sessions/{sid}/answers",
        json={"answers": [{"question": 1, "value": ["TRUE"]}]},
        headers=h(tok),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "session_closed"


async def test_speaking_start_is_409(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    _, tok = await make_user()
    v = await publish_test(db_session, Skill.SPEAKING)
    resp = await app_client.post(
        "/sessions", json={"test_id": str(v.test_id), "mode": "exam"}, headers=h(tok)
    )
    assert resp.status_code == 409


async def test_openapi_has_tags_and_operation_ids(app_client: AsyncClient) -> None:
    spec = (await app_client.get("/openapi.json")).json()
    assert {"tests", "sessions"} <= {t["name"] for t in spec["tags"]}
    operation_ids = {op["operationId"] for path in spec["paths"].values() for op in path.values()}
    assert {
        "list_tests",
        "get_test",
        "start_session",
        "list_sessions",
        "get_session",
        "save_answers",
        "update_session",
        "submit_session",
    } <= operation_ids
