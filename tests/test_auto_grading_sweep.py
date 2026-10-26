import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from core.models import Module, SessionMode, Skill, TestSession, TestVersion, User
from core.schemas.session import AnswerInput
from core.services import sessions
from core.settings import get_settings
from tests.content_factories import reading_key
from tests.helpers import correct_answers, publish_test

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
Factory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


class RecordingLog:
    def __init__(self) -> None:
        self.events: list[str] = []

    def exception(self, event: str, **kw: Any) -> None:
        self.events.append(event)

    def info(self, event: str, **kw: Any) -> None:
        self.events.append(event)


def _boom(ts: TestSession, version: TestVersion, answers: dict[int, Any]) -> Any:
    raise RuntimeError("marking bug")


@pytest.fixture
def factory(_engine: AsyncEngine) -> Factory:
    return async_sessionmaker(_engine, expire_on_commit=False)


async def submitted(
    factory: Factory,
    user: User,
    answers: dict[int, Any],
    *,
    slug: str | None = None,
    fail: bool = False,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> TestSession:
    """A submitted Reading exam; with `fail`, marking throws so it is left unscored."""
    async with factory() as db:
        v = await publish_test(db, Skill.READING, slug=slug)
        ts = await sessions.start_session(db, user, v.test_id, SessionMode.EXAM, NOW)
        if answers:
            inputs = [AnswerInput(question=q, value=a) for q, a in answers.items()]
            await sessions.save_answers(db, user, ts.id, inputs, NOW)
        if not fail:
            return await sessions.submit_session(db, user, ts.id, NOW)
        assert monkeypatch is not None
        with monkeypatch.context() as m:
            m.setattr(sessions, "mark_session", _boom)
            m.setattr(sessions, "log", RecordingLog())
            return await sessions.submit_session(db, user, ts.id, NOW)


async def score_of(factory: Factory, ts: TestSession) -> tuple[int | None, D | None]:
    async with factory() as db:
        row = await db.get(TestSession, ts.id)
        assert row is not None
        return row.raw_score, row.band


async def set_score(factory: Factory, ts: TestSession, raw: int, band: str) -> None:
    async with factory() as db:
        await db.execute(
            update(TestSession).where(TestSession.id == ts.id).values(raw_score=raw, band=D(band))
        )
        await db.commit()


async def test_rescore_fills_unscored_only(
    factory: Factory, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _ = await make_user()
    full = dict(correct_answers(Skill.READING))
    unscored = await submitted(factory, user, full, fail=True, monkeypatch=monkeypatch)
    scored = await submitted(factory, user, full)
    assert await score_of(factory, unscored) == (None, None)
    await set_score(factory, scored, 1, "1.0")

    counts = await sessions.rescore_sessions(factory)
    assert counts.scored >= 1
    assert await score_of(factory, unscored) == (40, D("9.0"))
    assert await score_of(factory, scored) == (1, D("1.0"))


async def test_rescore_everything_overwrites(factory: Factory, make_user: MakeUser) -> None:
    user, _ = await make_user()
    ts = await submitted(factory, user, dict(correct_answers(Skill.READING)))
    await set_score(factory, ts, 1, "1.0")
    await sessions.rescore_sessions(factory, everything=True)
    assert await score_of(factory, ts) == (40, D("9.0"))


async def test_rescore_counts_failures(
    factory: Factory, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _ = await make_user()
    ts = await submitted(factory, user, {}, fail=True, monkeypatch=monkeypatch)
    real = sessions.mark_session

    def fails_for_one(row: TestSession, version: TestVersion, answers: dict[int, Any]) -> Any:
        if row.id == ts.id:
            raise RuntimeError("still broken")
        return real(row, version, answers)

    log = RecordingLog()
    monkeypatch.setattr(sessions, "mark_session", fails_for_one)
    monkeypatch.setattr(sessions, "log", log)
    counts = await sessions.rescore_sessions(factory)
    assert counts.failed >= 1
    assert "auto_grading_failed" in log.events
    assert await score_of(factory, ts) == (None, None)


async def test_rescore_uses_the_sessions_own_version(factory: Factory, make_user: MakeUser) -> None:
    user, _ = await make_user()
    slug = f"rescore-{uuid7()}"
    first_30 = {q: a for q, a in correct_answers(Skill.READING).items() if q <= 30}
    ts = await submitted(factory, user, first_30, slug=slug)
    assert await score_of(factory, ts) == (30, D("7.0"))

    new_key = reading_key() | {1: ["FALSE"]}
    async with factory() as db:
        await publish_test(
            db, Skill.READING, slug=slug, key=new_key, module=Module.GENERAL_TRAINING
        )
    await sessions.rescore_sessions(factory, everything=True)
    assert await score_of(factory, ts) == (30, D("7.0"))


async def test_startup_scoring_survives_errors(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(factory: Factory, *, everything: bool = False) -> sessions.RescoreCounts:
        raise RuntimeError("database blip")

    log = RecordingLog()
    monkeypatch.setattr(sessions, "rescore_sessions", broken)
    monkeypatch.setattr(sessions, "log", log)
    await sessions.run_startup_scoring(factory)
    assert log.events == ["startup_scoring_failed"]


async def test_startup_scoring_logs_counts(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(factory: Factory, *, everything: bool = False) -> sessions.RescoreCounts:
        return sessions.RescoreCounts(scored=3, failed=1)

    log = RecordingLog()
    monkeypatch.setattr(sessions, "rescore_sessions", fake)
    monkeypatch.setattr(sessions, "log", log)
    await sessions.run_startup_scoring(factory)
    assert log.events == ["startup_scoring_done"]


async def test_lifespan_runs_startup_scoring(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.main

    started = asyncio.Event()

    async def fake(factory: Any) -> None:
        started.set()

    monkeypatch.setattr(get_settings(), "startup_scoring_enabled", True)
    monkeypatch.setattr(api.main, "run_startup_scoring", fake)
    async with LifespanManager(api.main.create_app()):
        await asyncio.wait_for(started.wait(), timeout=1)


async def test_lifespan_skips_startup_scoring_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.main

    started: list[bool] = []

    async def fake(factory: Any) -> None:
        started.append(True)

    monkeypatch.setattr(get_settings(), "startup_scoring_enabled", False)
    monkeypatch.setattr(api.main, "run_startup_scoring", fake)
    async with LifespanManager(api.main.create_app()):
        await asyncio.sleep(0.05)
    assert started == []
