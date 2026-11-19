"""users and rate_limits

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18

`rate_limits` holds the login limiter's counts (#2). Orbit Ops keeps them in
process memory, which is only right for one user and one process; this app is
multi-user from the start, so they live where every process and every restart
sees them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        # Stored lower-cased; the login form lower-cases what it's given.
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("session_token", sa.String(64), nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(email)", name="users_email_lowercase"),
    )
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("hits", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
    op.drop_table("users")
