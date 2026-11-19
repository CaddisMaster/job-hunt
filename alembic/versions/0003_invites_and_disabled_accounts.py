"""invites and disabled accounts

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18

An invite is a one-time set-password link (#3). The same link resets the
password of an account that already exists, so the admin never picks or sees
anyone's password. Only the token's SHA-256 is stored: the link is shown once.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "invites",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, index=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(email)", name="invites_email_lowercase"),
    )


def downgrade() -> None:
    op.drop_table("invites")
    op.drop_column("users", "disabled_at")
