"""tests and sessions

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24 10:46:30.877716
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tests",
        sa.Column("skill", sa.String(length=16), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "skill IN ('reading', 'listening', 'writing', 'speaking')", name=op.f("ck_tests_skill")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tests")),
        sa.UniqueConstraint("slug", name=op.f("uq_tests_slug")),
    )
    op.create_table(
        "daily_usage",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("skill", sa.String(length=16), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "skill IN ('reading', 'listening', 'writing', 'speaking')",
            name=op.f("ck_daily_usage_skill"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_daily_usage_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "skill", "day", name=op.f("pk_daily_usage")),
    )
    op.create_table(
        "test_versions",
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answer_key", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("staff_content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name=op.f("ck_test_versions_status")
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
            name=op.f("fk_test_versions_test_id_tests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_versions")),
        sa.UniqueConstraint("test_id", "version", name="uq_test_versions_test_id_version"),
    )
    op.create_index(
        "uq_test_versions_one_draft",
        "test_versions",
        ["test_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    # tests and test_versions reference each other, so this FK is added once both exist.
    op.create_foreign_key(
        op.f("fk_tests_current_version_id_test_versions"),
        "tests",
        "test_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "test_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("test_version_id", sa.Uuid(), nullable=False),
        sa.Column("skill", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_reason", sa.String(length=16), nullable=True),
        sa.Column("request_manual_grading", sa.Boolean(), nullable=False),
        sa.Column("grading_method", sa.String(length=16), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "grading_method IN ('auto', 'ai', 'human')",
            name=op.f("ck_test_sessions_grading_method"),
        ),
        sa.CheckConstraint("mode IN ('practice', 'exam')", name=op.f("ck_test_sessions_mode")),
        sa.CheckConstraint(
            "skill IN ('reading', 'listening', 'writing', 'speaking')",
            name=op.f("ck_test_sessions_skill"),
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'submitted')", name=op.f("ck_test_sessions_status")
        ),
        sa.CheckConstraint(
            "submit_reason IN ('student', 'timeout')", name=op.f("ck_test_sessions_submit_reason")
        ),
        sa.ForeignKeyConstraint(
            ["test_version_id"],
            ["test_versions.id"],
            name=op.f("fk_test_sessions_test_version_id_test_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_test_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_sessions")),
    )
    op.create_index(
        "ix_test_sessions_status_deadline", "test_sessions", ["status", "deadline"], unique=False
    )
    op.create_index(
        "ix_test_sessions_user_id_created_at",
        "test_sessions",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "answers",
        sa.Column("test_session_id", sa.Uuid(), nullable=False),
        sa.Column("question_number", sa.Integer(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["test_session_id"],
            ["test_sessions.id"],
            name=op.f("fk_answers_test_session_id_test_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answers")),
        sa.UniqueConstraint(
            "test_session_id", "question_number", name="uq_answers_test_session_id_question_number"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_tests_current_version_id_test_versions"), "tests", type_="foreignkey"
    )
    op.drop_table("answers")
    op.drop_index("ix_test_sessions_user_id_created_at", table_name="test_sessions")
    op.drop_index("ix_test_sessions_status_deadline", table_name="test_sessions")
    op.drop_table("test_sessions")
    op.drop_index(
        "uq_test_versions_one_draft",
        table_name="test_versions",
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.drop_table("test_versions")
    op.drop_table("daily_usage")
    op.drop_table("tests")
