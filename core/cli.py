"""Admin command line.

- ``python -m core.cli set-role <email> <role>``
- ``python -m core.cli load-test <file.json> [--publish]``
- ``python -m core.cli regrade-auto [--all]``
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db import async_session_factory
from core.exceptions import NotFoundError, ValidationAppError
from core.models import Role
from core.services.sessions import rescore_sessions
from core.services.test_loader import load_test_file
from core.services.users import set_role_by_email

# Same normalization registration applies (EmailStr lowercases the domain), so the email
# can be typed exactly as the user registered it.
_email = TypeAdapter(EmailStr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m core.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    set_role = commands.add_parser("set-role", help="set a user's role")
    set_role.add_argument("email")
    set_role.add_argument("role", choices=[r.value for r in Role])
    load_test = commands.add_parser("load-test", help="load a test from a JSON file as a draft")
    load_test.add_argument("path", type=Path)
    load_test.add_argument("--publish", action="store_true", help="publish it if it has no errors")
    regrade = commands.add_parser(
        "regrade-auto", help="score Reading/Listening sessions that have no score"
    )
    regrade.add_argument(
        "--all", action="store_true", help="re-score every session, overwriting stored scores"
    )
    return parser


async def run(
    argv: Sequence[str],
    session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "load-test":
        return await _load_test(args.path, args.publish, session_factory)
    if args.command == "regrade-auto":
        return await _regrade_auto(args.all, session_factory)
    return await _set_role(args.email, args.role, session_factory)


async def _set_role(
    raw_email: str, role: str, session_factory: async_sessionmaker[AsyncSession]
) -> int:
    try:
        email = _email.validate_python(raw_email)
    except ValidationError:
        print(f"invalid email address {raw_email}", file=sys.stderr)
        return 1
    async with session_factory() as session:
        try:
            await set_role_by_email(session, email, Role(role))
        except NotFoundError:
            print(f"no user with email {raw_email}", file=sys.stderr)
            return 1
    print(f"set role of {raw_email} to {role}")
    return 0


async def _load_test(
    path: Path, publish: bool, session_factory: async_sessionmaker[AsyncSession]
) -> int:
    async with session_factory() as session:
        try:
            result = await load_test_file(session, path, publish=publish)
        except ValidationAppError as exc:
            print(exc.message, file=sys.stderr)
            return 1
    print(f"loaded {result.slug} v{result.version.version} ({result.version.status})")
    for p in result.problems:
        print(f"{p.severity} {p.code} section={p.section} question={p.question}: {p.message}")
    return 1 if publish and not result.published else 0


async def _regrade_auto(everything: bool, session_factory: async_sessionmaker[AsyncSession]) -> int:
    counts = await rescore_sessions(session_factory, everything=everything)
    print(f"scored {counts.scored} sessions")
    if counts.failed:
        print(f"failed to score {counts.failed} sessions (see log)", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
