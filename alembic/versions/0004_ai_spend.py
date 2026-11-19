"""ai spend ledger

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19

One row per Claude call that came back with a bill (#22). Every call runs on
Sean's API key, so each user's monthly cap and the key's are summed from here.
user_id is SET NULL: deleting a user never shrinks a month's spend.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_spend",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_microcents", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cache_read_tokens >= 0 AND cache_write_tokens >= 0",
            name="ai_spend_tokens",
        ),
        sa.CheckConstraint("cost_microcents >= 0", name="ai_spend_cost"),
    )
    op.create_index("ai_spend_user_month", "ai_spend", ["user_id", "created_at"])
    op.create_index("ai_spend_month", "ai_spend", ["created_at"])


def downgrade() -> None:
    op.drop_index("ai_spend_month", table_name="ai_spend")
    op.drop_index("ai_spend_user_month", table_name="ai_spend")
    op.drop_table("ai_spend")
