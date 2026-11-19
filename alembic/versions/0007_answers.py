"""answer bank

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19

A user's answers to what applications ask that a resume doesn't say (#25):
work authorization, salary, start date, EEO and the like. One row per answer,
so a new question needs no migration. Private to its owner.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("question", sa.Text, nullable=True),
        sa.Column("answer", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "key", name="answers_user_key"),
    )


def downgrade() -> None:
    op.drop_table("answers")
