"""tailored_resumes

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20

A resume tailored to one saved job (#73). **Private** (`user_id`, non-negotiable 1).
One per saved job: making a new one replaces it, and deleting the saved job deletes
it.

`content` is what the PDF shows, structured (name, contact, summary, roles with their
bullets, skills, education, certifications), every line already checked against
the resume (non-negotiable 7). `left_out` lists what the model wrote that the resume
doesn't support, shown to the user, never printed. `pdf` is made from `content`
when it's built. `approved_at` is set when the user approves it; only an approved
resume is used to apply (#74, #66).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tailored_resumes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("saved_job_id", sa.Integer, sa.ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_id", sa.Integer, sa.ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", postgresql.JSONB, nullable=False),
        sa.Column("left_out", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("pdf", sa.LargeBinary, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("saved_job_id", name="tailored_resumes_saved_job"),
    )
    op.create_index("tailored_resumes_user", "tailored_resumes", ["user_id"])


def downgrade() -> None:
    op.drop_index("tailored_resumes_user", table_name="tailored_resumes")
    op.drop_table("tailored_resumes")
