import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from uuid_utils.compat import uuid7

from core.cli import build_parser, run
from core.models import User
from core.services import auth


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
