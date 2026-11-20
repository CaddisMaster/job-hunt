"""postings

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19

The jobs the nightly crawl finds (#44): one row per job per source, unique on
(source, external_id), so a job seen again is updated, not duplicated. Shared by
every user, so no user_id. `area` is app/area.py's answer for the posting's
location; `unknown` ones are kept but shown to no one. `missed_fetches` counts the
successful fetches of its employer in a row that didn't return it: at 3 the
posting is closed (`closed_at`), kept for history, and reopened if it comes back.
`description` is plain text, never markup, capped at 50,000 characters.
`source` already allows the aggregators (#45), which have no employer row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "postings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("employer_id", sa.Integer, sa.ForeignKey("employers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("location", sa.String(500), nullable=False, server_default=""),
        sa.Column("area", sa.String(16), nullable=False),
        sa.Column("latitude", sa.Float, nullable=True),
        sa.Column("longitude", sa.Float, nullable=True),
        sa.Column("remote", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("missed_fetches", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source IN ('greenhouse', 'lever', 'ashby', 'adzuna', 'usajobs')", name="postings_source"
        ),
        sa.CheckConstraint("area IN ('in_area', 'remote', 'out_of_area', 'unknown')", name="postings_area"),
        sa.CheckConstraint("char_length(description) <= 50000", name="postings_description_length"),
        sa.UniqueConstraint("source", "external_id", name="postings_source_job"),
    )
    op.create_index("postings_employer", "postings", ["employer_id"])
    op.create_index("postings_area_open", "postings", ["area", "closed_at"])


def downgrade() -> None:
    op.drop_index("postings_area_open", table_name="postings")
    op.drop_index("postings_employer", table_name="postings")
    op.drop_table("postings")
