"""resumes

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19

Uploaded resumes (#23), private to their owner. The file is stored here as
bytea, at most 5 MB, so the nightly dump (and its encryption, #21) covers
resumes with nothing else to back up. Deleting a user deletes their resumes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 5242880", name="resumes_size"),
        sa.CheckConstraint("kind IN ('pdf', 'docx')", name="resumes_kind"),
    )
    op.create_index("resumes_user_newest", "resumes", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("resumes_user_newest", table_name="resumes")
    op.drop_table("resumes")
