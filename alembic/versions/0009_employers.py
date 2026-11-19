"""employers

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19

The shared registry of Pittsburgh-area employers (#43): each one's hiring
system (Greenhouse, Lever or Ashby) and the board token its public job API is
read by. Shared by every user, so no user_id; `added_by` is the admin who added
it. The nightly crawl (#44) fetches the enabled ones and fills `last_fetched_at`
and `last_error`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("system", sa.String(16), nullable=False),
        sa.Column("token", sa.String(100), nullable=False),
        sa.Column("careers_url", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("added_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.CheckConstraint("system IN ('greenhouse', 'lever', 'ashby')", name="employers_system"),
        sa.UniqueConstraint("system", "token", name="employers_board"),
    )


def downgrade() -> None:
    op.drop_table("employers")
