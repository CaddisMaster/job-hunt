"""saved_jobs

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-20

Jobs a user pasted a link to (#63): the only way jobs get into Job Hunt (plan §10
decision 10). **Private** to that user (`user_id`, non-negotiable 1). One row per
user per link.

`source` says how the posting was read: the page's schema.org JobPosting (`page`) or
text the user pasted (`text`). `pending` is a link not yet read, or one that couldn't
be read (`read_error` says why, for the user) and waits for its text. `description`
is plain text, never markup, capped at 50,000 characters.

Numbered 0009 on 2026-11-20: 0009–0011 (employers, postings, workday) were removed
before they reached production, which was at 0008, along with the crawl they served
(#71). This was 0012, with an `area` column and the hiring systems as sources, and
never reached production either.
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
        "saved_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("read_error", sa.String(300), nullable=True),
        sa.Column("company", sa.String(200), nullable=False, server_default=""),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("location", sa.String(500), nullable=False, server_default=""),
        sa.Column("remote", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "source IN ('pending', 'page', 'text')", name="saved_jobs_source"
        ),
        sa.CheckConstraint("char_length(description) <= 50000", name="saved_jobs_description_length"),
        sa.UniqueConstraint("user_id", "url", name="saved_jobs_user_url"),
    )


def downgrade() -> None:
    op.drop_table("saved_jobs")
