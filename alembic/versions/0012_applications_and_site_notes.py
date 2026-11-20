"""applications and site_notes

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-20

What the /apply skill records through the bridge (#76). Both **private**
(`user_id`, non-negotiable 1).

`applications`: one per saved job, written when a session finishes with it.
`outcome` is `submitted` (Sean clicked Submit himself, non-negotiable 8) or
`stopped` (`note` says why and what's left). `answers_used` is what was filled
in, for Sean to check against his answer bank (non-negotiable 7). `screenshot` is
the confirmation page, a PNG, capped at 5 MB. Nothing on the server ever touches the
employer's site: this is a record, written after the fact.

`site_notes`: what Claude learned about one site's application flow ("Workday:
make an account first; work history is three pages"), one per host per user, read
at the start of the next application there so it goes faster.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("saved_job_id", sa.Integer, sa.ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("answers_used", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("screenshot", sa.LargeBinary, nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("outcome IN ('submitted', 'stopped')", name="applications_outcome"),
        sa.CheckConstraint("char_length(note) <= 4000", name="applications_note_length"),
        sa.CheckConstraint("octet_length(screenshot) <= 5242880", name="applications_screenshot_size"),
        sa.UniqueConstraint("saved_job_id", name="applications_saved_job"),
    )
    op.create_index("applications_user", "applications", ["user_id"])
    op.create_table(
        "site_notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("char_length(note) <= 20000", name="site_notes_note_length"),
        sa.UniqueConstraint("user_id", "host", name="site_notes_user_host"),
    )


def downgrade() -> None:
    op.drop_table("site_notes")
    op.drop_index("applications_user", table_name="applications")
    op.drop_table("applications")
