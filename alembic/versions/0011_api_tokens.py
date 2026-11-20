"""api_tokens

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-20

Personal access tokens for the bridge (#74): how Claude Code on Sean's Mac calls
Job Hunt's JSON API. **Private** (`user_id`, non-negotiable 1).

Only `token_hash` (SHA-256, hex) is stored: the token is a long random string
shown once, so a fast hash is enough, as for any API key. `prefix` is its first
characters, to tell tokens apart on the page. A token works until `revoked_at`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="api_tokens_hash"),
    )
    op.create_index("api_tokens_user", "api_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("api_tokens_user", table_name="api_tokens")
    op.drop_table("api_tokens")
