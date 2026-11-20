"""runs and run_events

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-20

Web-driven Claude Code runs (#88). Both **private** (`user_id`, non-negotiable 1).

`runs`: a button in the web app queues one (`apply` for a saved job, or `profile`);
the runner on the VM claims the oldest queued run, starts Claude Code headless, and
finishes it as `done`, `failed` or `stopped` with a short summary. `stop_requested`
is Sean's Stop button, seen by the run's next wait.

`run_events`: what the run shows on the job's page. `progress` is a line of news.
`question`, `browser` and `submit` are prompts that wait for Sean: his `answer`
(the reply; "done" for the browser; "submit" or "stop" for submit) and
`answered_at`, or `cancelled` when the run stopped first. A `submit` prompt carries
the filled form's screenshot (PNG, ≤ 5 MB).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("saved_job_id", sa.Integer, sa.ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("stop_requested", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('apply', 'profile')", name="runs_kind"),
        sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed', 'stopped')", name="runs_status"),
        sa.CheckConstraint("(kind = 'apply') = (saved_job_id IS NOT NULL)", name="runs_apply_has_job"),
        sa.CheckConstraint("char_length(summary) <= 2000", name="runs_summary_length"),
    )
    op.create_index("runs_user_status", "runs", ["user_id", "status", "created_at"])
    op.create_index("runs_saved_job", "runs", ["saved_job_id"])
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("options", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("screenshot", sa.LargeBinary, nullable=True),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('progress', 'question', 'browser', 'submit')", name="run_events_kind"),
        sa.CheckConstraint("char_length(text) <= 4000", name="run_events_text_length"),
        sa.CheckConstraint("char_length(answer) <= 2000", name="run_events_answer_length"),
        sa.CheckConstraint("octet_length(screenshot) <= 5242880", name="run_events_screenshot_size"),
    )
    op.create_index("run_events_run", "run_events", ["run_id", "id"])


def downgrade() -> None:
    op.drop_index("run_events_run", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("runs_saved_job", table_name="runs")
    op.drop_index("runs_user_status", table_name="runs")
    op.drop_table("runs")
