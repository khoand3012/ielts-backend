import dataclasses
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import SessionMode, Skill, TestSession, TestVersion, User
from core.schemas.session import AnswerInput
from core.services import sessions
from tests.helpers import correct_answers, publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


class _SilentLog:
    def exception(self, event: str, **kw: Any) -> None:
        return None


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def start(
    db: AsyncSession,
    user: User,
    skill: Skill,
    mode: SessionMode = SessionMode.EXAM,
    now: datetime = NOW,
    answers: dict[int, Any] | None = None,
) -> TestSession:
    v = await publish_test(db, skill)
    ts = await sessions.start_session(db, user, v.test_id, mode, now)
    if answers:
        inputs = [AnswerInput(question=q, value=a) for q, a in answers.items()]
        await sessions.save_answers(db, user, ts.id, inputs, now)
    return ts


async def result(client: AsyncClient, tok: str, ts: TestSession) -> dict[str, Any]:
    resp = await client.get(f"/sessions/{ts.id}/result", headers=h(tok))
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def rows(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {q["number"]: q for q in body["questions"]}


async def test_result_graded_listening(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, tok = await make_user()
    answers: dict[int, Any] = dict(correct_answers(Skill.LISTENING))
    answers |= {21: ["B", "C"], 31: " Word31 "}
    ts = await start(db_session, user, Skill.LISTENING, answers=answers)
    await sessions.submit_session(db_session, user, ts.id, NOW + timedelta(minutes=20))

    body = await result(app_client, tok, ts)
    assert body["status"] == "graded"
    assert (body["raw_score"], body["max_score"], body["answered"]) == (39, 40, 40)
    assert body["band"] == 9.0
    assert (body["skill"], body["module"], body["mode"]) == ("listening", None, "exam")
    assert body["time_spent_seconds"] == 20 * 60
    assert sum(q["marks"] for q in body["questions"]) == body["raw_score"]
    numbers = [q["number"] for q in body["questions"]]
    assert numbers == sorted(numbers)

    by = rows(body)
    assert by[21] == {
        "number": 21,
        "numbers": [21, 22],
        "kind": "choice",
        "answer": ["B", "C"],
        "accepted": ["B", "D"],
        "correct_choices": ["B"],
        "marks": 1,
        "max_marks": 2,
    }
    assert (by[31]["answer"], by[31]["accepted"], by[31]["marks"]) == (
        " Word31 ",
        ["word31", "alt31"],
        1,
    )
    assert by[31]["correct_choices"] is None


async def test_result_partial_practice(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, tok = await make_user()
    ten = {q: a for q, a in correct_answers(Skill.READING).items() if q <= 10}
    ts = await start(db_session, user, Skill.READING, SessionMode.PRACTICE, answers=ten)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    body = await result(app_client, tok, ts)
    assert body["status"] == "graded"
    assert (body["raw_score"], body["answered"], body["band"]) == (10, 10, None)
    assert body["module"] == "academic"


async def test_result_of_empty_practice(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, tok = await make_user()
    ts = await start(db_session, user, Skill.LISTENING, SessionMode.PRACTICE)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    body = await result(app_client, tok, ts)
    assert (body["status"], body["raw_score"], body["band"]) == ("graded", 0, None)
    assert all(q["answer"] is None for q in body["questions"])
    assert rows(body)[21]["correct_choices"] == []


async def test_result_in_progress_is_409(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, tok = await make_user()
    ts = await start(db_session, user, Skill.READING, SessionMode.PRACTICE)
    resp = await app_client.get(f"/sessions/{ts.id}/result", headers=h(tok))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_result_other_user_404(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    _, other = await make_user()
    ts = await start(db_session, user, Skill.READING)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    resp = await app_client.get(f"/sessions/{ts.id}/result", headers=h(other))
    assert resp.status_code == 404


async def test_result_requires_auth(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    ts = await start(db_session, user, Skill.READING)
    assert (await app_client.get(f"/sessions/{ts.id}/result")).status_code == 401


async def test_result_submits_expired_exam(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, tok = await make_user()
    started = datetime.now(UTC) - timedelta(hours=2)
    ts = await start(db_session, user, Skill.READING, now=started)
    body = await result(app_client, tok, ts)
    assert (body["status"], body["submit_reason"]) == ("graded", "timeout")
    assert (body["raw_score"], body["band"]) == (0, 0.0)
    assert body["time_spent_seconds"] == 3600


async def test_result_time_spent_practice(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, tok = await make_user()
    ts = await start(db_session, user, Skill.READING, SessionMode.PRACTICE)
    await sessions.submit_session(db_session, user, ts.id, NOW + timedelta(seconds=125))
    body = await result(app_client, tok, ts)
    assert body["time_spent_seconds"] == 125
    assert (body["mode"], body["submit_reason"]) == ("practice", "student")


async def test_result_writing_being_graded(
    app_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, tok = await make_user()
    ts = await start(db_session, user, Skill.WRITING)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    body = await result(app_client, tok, ts)
    assert body["status"] == "being_graded"
    nulls = ("raw_score", "max_score", "band", "answered", "questions")
    assert {k: body[k] for k in nulls} == dict.fromkeys(nulls)


async def test_result_failed_scoring_being_graded(
    app_client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(row: TestSession, version: TestVersion, answers: dict[int, Any]) -> Any:
        raise RuntimeError("marking bug")

    user, tok = await make_user()
    ts = await start(db_session, user, Skill.READING)
    with monkeypatch.context() as m:
        m.setattr(sessions, "mark_session", boom)
        m.setattr(sessions, "log", _SilentLog())
        await sessions.submit_session(db_session, user, ts.id, NOW)
    body = await result(app_client, tok, ts)
    assert (body["status"], body["raw_score"], body["questions"]) == ("being_graded", None, None)


async def test_openapi_has_result_route(app_client: AsyncClient) -> None:
    spec = (await app_client.get("/openapi.json")).json()
    op = spec["paths"]["/sessions/{session_id}/result"]["get"]
    assert op["operationId"] == "get_session_result"
    assert op["tags"] == ["sessions"]


class RecordingLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def exception(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


async def test_result_survives_marking_failure_on_read(
    app_client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(row: TestSession, version: TestVersion, answers: dict[int, Any]) -> Any:
        raise RuntimeError("marking bug after a deploy")

    user, tok = await make_user()
    full = dict(correct_answers(Skill.READING))
    ts = await start(db_session, user, Skill.READING, answers=full)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    log = RecordingLog()
    monkeypatch.setattr(sessions, "mark_session", boom)
    monkeypatch.setattr(sessions, "log", log)

    body = await result(app_client, tok, ts)
    assert (body["status"], body["raw_score"], body["band"]) == ("graded", 40, 9.0)
    assert (body["questions"], body["answered"]) == (None, None)
    assert [e for e, _ in log.events] == ["auto_grading_failed"]


async def test_result_logs_drift_and_keeps_stored_total(
    app_client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, tok = await make_user()
    full = dict(correct_answers(Skill.READING))
    ts = await start(db_session, user, Skill.READING, answers=full)
    await sessions.submit_session(db_session, user, ts.id, NOW)
    real = sessions.mark_session

    def changed_rules(row: TestSession, version: TestVersion, answers: dict[int, Any]) -> Any:
        return dataclasses.replace(real(row, version, answers), raw_score=39)

    log = RecordingLog()
    monkeypatch.setattr(sessions, "mark_session", changed_rules)
    monkeypatch.setattr(sessions, "log", log)

    body = await result(app_client, tok, ts)
    assert (body["raw_score"], body["band"]) == (40, 9.0)
    assert log.events == [
        ("auto_grading_drift", {"session_id": str(ts.id), "stored": 40, "recomputed": 39})
    ]
