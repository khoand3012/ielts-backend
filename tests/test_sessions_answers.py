from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import SessionClosedError, ValidationAppError
from core.models import SessionMode, Skill, TestSession, User
from core.schemas.session import AnswerInput
from core.services import sessions
from tests.helpers import publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]


async def practice(db: AsyncSession, user: User, skill: Skill) -> TestSession:
    v = await publish_test(db, skill)
    return await sessions.start_session(db, user, v.test_id, SessionMode.PRACTICE)


async def save(db: AsyncSession, user: User, ts: TestSession, *pairs: tuple[int, Any]) -> Any:
    answers = [AnswerInput(question=q, value=v) for q, v in pairs]
    return await sessions.save_answers(db, user, ts.id, answers)


async def rejects(db: AsyncSession, user: User, ts: TestSession, q: int, value: Any) -> None:
    with pytest.raises(ValidationAppError) as exc:
        await save(db, user, ts, (q, value))
    assert exc.value.extra["question"] == q


async def test_answers_are_upserted(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.LISTENING)
    await save(db_session, user, ts, (1, "pottery"))
    saved = await save(db_session, user, ts, (1, "potter"), (2, "Wednesday"))
    assert saved == {1: "potter", 2: "Wednesday"}


async def test_null_clears_an_answer(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.LISTENING)
    await save(db_session, user, ts, (1, "pottery"))
    assert await save(db_session, user, ts, (1, None)) == {}


async def test_choose_two_saved_under_first_number(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.LISTENING)
    assert await save(db_session, user, ts, (21, ["D", "B"])) == {21: ["D", "B"]}
    await rejects(db_session, user, ts, 22, ["B"])
    await rejects(db_session, user, ts, 21, ["B", "B"])
    await rejects(db_session, user, ts, 21, ["A", "B", "C"])
    await rejects(db_session, user, ts, 21, ["Z"])
    await rejects(db_session, user, ts, 21, "B")


async def test_single_multiple_choice_is_a_list(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.LISTENING)
    assert await save(db_session, user, ts, (11, ["A"])) == {11: ["A"]}
    await rejects(db_session, user, ts, 11, ["A", "B"])


async def test_matching_takes_one_option_key(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.LISTENING)
    assert await save(db_session, user, ts, (16, "C")) == {16: "C"}
    await rejects(db_session, user, ts, 16, "Z")
    await rejects(db_session, user, ts, 16, ["C"])


async def test_text_and_essay_lengths(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    listening = await practice(db_session, user, Skill.LISTENING)
    await rejects(db_session, user, listening, 1, "x" * 201)
    await rejects(db_session, user, listening, 1, ["pottery"])

    writing = await practice(db_session, user, Skill.WRITING)
    essay = " ".join(["word"] * 1000)
    assert await save(db_session, user, writing, (2, essay)) == {2: essay}
    await rejects(db_session, user, writing, 1, "x" * 20001)
    await rejects(db_session, user, writing, 3, "no task 3")


async def test_unknown_question_rejected(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.READING)
    await rejects(db_session, user, ts, 41, "x")


async def test_bad_input_writes_nothing(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.LISTENING)
    with pytest.raises(ValidationAppError):
        await save(db_session, user, ts, (1, "pottery"), (16, "Z"))
    assert await sessions.get_answers(db_session, ts) == {}


async def test_save_to_submitted_session_is_409(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.READING)
    ts.status = "submitted"
    await db_session.commit()
    with pytest.raises(SessionClosedError) as exc:
        await save(db_session, user, ts, (1, ["TRUE"]))
    assert exc.value.status_code == 409
    assert exc.value.code == "session_closed"


async def test_manual_grading_toggle_writing_only(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    reading = await practice(db_session, user, Skill.READING)
    with pytest.raises(ValidationAppError):
        await sessions.set_manual_grading(db_session, user, reading.id, True)

    writing = await practice(db_session, user, Skill.WRITING)
    ts = await sessions.set_manual_grading(db_session, user, writing.id, True)
    assert ts.request_manual_grading is True
    ts = await sessions.set_manual_grading(db_session, user, writing.id, False)
    assert ts.request_manual_grading is False


async def test_toggle_on_submitted_session_is_409(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.WRITING)
    ts.status = "submitted"
    await db_session.commit()
    with pytest.raises(SessionClosedError):
        await sessions.set_manual_grading(db_session, user, ts.id, True)


async def test_empty_values_clear_an_answer(db_session: AsyncSession, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await practice(db_session, user, Skill.LISTENING)
    await save(db_session, user, ts, (11, ["A"]), (1, "pottery"), (2, "Wednesday"))
    # unticking the only option sends [], clearing a text box sends "" or just spaces
    assert await save(db_session, user, ts, (11, []), (1, ""), (2, "   ")) == {}
