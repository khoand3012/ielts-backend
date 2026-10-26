import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from uuid_utils.compat import uuid7

from core.cli import build_parser, run
from core.models import SessionMode, Skill, TestSession, TestVersion, User
from core.services import auth, sessions
from core.services.sessions import RescoreCounts
from tests.content_factories import listening_content, listening_key, reading_content, reading_key
from tests.helpers import publish_test


class _SilentLog:
    def exception(self, event: str, **kw: Any) -> None:
        return None


async def test_cli_sets_role(_engine: AsyncEngine, capsys: pytest.CaptureFixture[str]) -> None:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    email = f"cli-{uuid7()}@t.com"
    async with factory() as session:
        user = await auth.register(session, email, "password123")

    assert await run(["set-role", email, "admin"], factory) == 0
    assert f"set role of {email} to admin" in capsys.readouterr().out

    async with factory() as session:
        reloaded = await session.get(User, user.id)
        assert reloaded is not None
        assert reloaded.role == "admin"


async def test_cli_unknown_email(_engine: AsyncEngine, capsys: pytest.CaptureFixture[str]) -> None:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    assert await run(["set-role", f"nobody-{uuid7()}@t.com", "admin"], factory) == 1
    assert "no user with email" in capsys.readouterr().err


def test_cli_rejects_unknown_role() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["set-role", "a@t.com", "superuser"])
    assert exc.value.code == 2


async def test_cli_matches_email_as_typed_at_registration(
    _engine: AsyncEngine, app_client: AsyncClient
) -> None:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    typed = f"Cli.Mixed-{uuid7()}@Example.COM"
    resp = await app_client.post("/auth/register", json={"email": typed, "password": "password123"})
    assert resp.status_code == 201

    assert await run(["set-role", typed, "admin"], factory) == 0

    async with factory() as session:
        reloaded = await session.get(User, uuid.UUID(resp.json()["id"]))
        assert reloaded is not None
        assert reloaded.role == "admin"


async def test_cli_load_test_prints_problems(
    _engine: AsyncEngine,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_upload(key: str, path: Path, content_type: str) -> None:
        return None

    monkeypatch.setattr("core.services.storage.upload_file", fake_upload)
    content = listening_content()
    for part in content["parts"]:
        part["audio"] = None
    path = tmp_path / "listening.json"
    path.write_text(
        json.dumps(
            {
                "slug": f"cli-{uuid7()}",
                "title": "T",
                "skill": "listening",
                "content": content,
                "answer_key": listening_key(),
            }
        ),
        encoding="utf-8",
    )
    factory = async_sessionmaker(_engine, expire_on_commit=False)

    assert await run(["load-test", str(path), "--publish"], factory) == 1
    out = capsys.readouterr().out
    assert "(draft)" in out
    assert "error missing_audio section=1" in out


async def test_cli_load_test_publishes(
    _engine: AsyncEngine, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "reading.json"
    slug = f"cli-{uuid7()}"
    path.write_text(
        json.dumps(
            {
                "slug": slug,
                "title": "T",
                "skill": "reading",
                "module": "academic",
                "content": reading_content(),
                "answer_key": reading_key(),
            }
        ),
        encoding="utf-8",
    )
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    assert await run(["load-test", str(path), "--publish"], factory) == 0
    assert f"loaded {slug} v1 (published)" in capsys.readouterr().out


async def test_cli_load_test_invalid_file(
    _engine: AsyncEngine, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"slug": "x"}', encoding="utf-8")
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    assert await run(["load-test", str(path)], factory) == 1
    assert "invalid test file" in capsys.readouterr().err


async def test_cli_regrade_auto(
    _engine: AsyncEngine,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(row: TestSession, version: TestVersion, answers: dict[int, Any]) -> Any:
        raise RuntimeError("marking bug")

    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as session:
        user = await auth.register(session, f"cli-{uuid7()}@t.com", "password123")
        v = await publish_test(session, Skill.READING)
        ts = await sessions.start_session(session, user, v.test_id, SessionMode.EXAM)
        with monkeypatch.context() as m:
            m.setattr(sessions, "mark_session", boom)
            m.setattr(sessions, "log", _SilentLog())
            await sessions.submit_session(session, user, ts.id)

    assert await run(["regrade-auto"], factory) == 0
    assert "scored" in capsys.readouterr().out
    async with factory() as session:
        row = await session.get(TestSession, ts.id)
        assert row is not None
        assert row.raw_score == 0


async def test_cli_regrade_auto_failure_exit_code(
    _engine: AsyncEngine,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def fake(factory: Any, *, everything: bool = False) -> RescoreCounts:
        calls.append(everything)
        return RescoreCounts(scored=2, failed=1)

    monkeypatch.setattr("core.cli.rescore_sessions", fake)
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    assert await run(["regrade-auto", "--all"], factory) == 1
    assert calls == [True]
    out = capsys.readouterr()
    assert "scored 2 sessions" in out.out
    assert "failed to score 1 sessions" in out.err
