"""auto-grading: version module, session score

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("test_versions", sa.Column("module", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        op.f("ck_test_versions_module"),
        "test_versions",
        "module IN ('academic', 'general_training')",
    )
    # Every Reading test so far is Academic.
    op.execute(
        "UPDATE test_versions SET module = 'academic' "
        "WHERE test_id IN (SELECT id FROM tests WHERE skill = 'reading')"
    )
    op.add_column("test_sessions", sa.Column("raw_score", sa.SmallInteger(), nullable=True))
    op.add_column("test_sessions", sa.Column("band", sa.Numeric(2, 1), nullable=True))
    op.create_check_constraint(
        op.f("ck_test_sessions_raw_score"), "test_sessions", "raw_score BETWEEN 0 AND 40"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_test_sessions_raw_score"), "test_sessions", type_="check")
    op.drop_column("test_sessions", "band")
    op.drop_column("test_sessions", "raw_score")
    op.drop_constraint(op.f("ck_test_versions_module"), "test_versions", type_="check")
    op.drop_column("test_versions", "module")
