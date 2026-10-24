"""Admin command line. Usage: ``python -m core.cli set-role <email> <role>``."""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db import async_session_factory
from core.exceptions import NotFoundError
from core.models import Role
from core.services.users import set_role_by_email


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m core.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    set_role = commands.add_parser("set-role", help="set a user's role")
    set_role.add_argument("email")
    set_role.add_argument("role", choices=[r.value for r in Role])
    return parser


async def run(
    argv: Sequence[str],
    session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
) -> int:
    args = build_parser().parse_args(argv)
    async with session_factory() as session:
        try:
            await set_role_by_email(session, args.email, Role(args.role))
        except NotFoundError:
            print(f"no user with email {args.email}", file=sys.stderr)
            return 1
    print(f"set role of {args.email} to {args.role}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
