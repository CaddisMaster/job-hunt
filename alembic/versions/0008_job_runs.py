"""job runs

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19

One row per run of a scheduled job in the worker (#41): which job, when it
started and finished, and how it went. `summary` holds counts, never personal
data. A row with no `finished_at` is a run in progress, or one the worker was
stopped in the middle of. Shared, not a user's data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.CheckConstraint("outcome IN ('ok', 'failed', 'skipped')", name="job_runs_outcome"),
        sa.CheckConstraint("(finished_at IS NULL) = (outcome IS NULL)", name="job_runs_finished"),
    )
    op.create_index("job_runs_job_newest", "job_runs", ["job", "started_at"])


def downgrade() -> None:
    op.drop_index("job_runs_job_newest", table_name="job_runs")
    op.drop_table("job_runs")
