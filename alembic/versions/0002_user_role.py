"""user role

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), server_default="student", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_users_role"), "users", "role IN ('student', 'teacher', 'admin')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.drop_column("users", "role")
