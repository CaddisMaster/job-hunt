"""profiles

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19

The structured profile Claude builds from a user's resume (#24), one per user,
private to them. Later steps use it only once its owner has reviewed it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("resume_id", sa.Integer, sa.ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("data", postgresql.JSONB, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("profiles")
