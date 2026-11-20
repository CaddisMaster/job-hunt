"""workday

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-19

Workday as a fourth hiring system (#59): `workday` is allowed in employers.system
and postings.source. A Workday employer's `token` is "<tenant>/wd<N>/<site>", read
from its careers URL (<tenant>.wd<N>.myworkdayjobs.com/<site>), so no new column.
Expand-only: each check constraint is replaced by a wider one.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPLOYERS_OLD = "system IN ('greenhouse', 'lever', 'ashby')"
EMPLOYERS_NEW = "system IN ('greenhouse', 'lever', 'ashby', 'workday')"
POSTINGS_OLD = "source IN ('greenhouse', 'lever', 'ashby', 'adzuna', 'usajobs')"
POSTINGS_NEW = "source IN ('greenhouse', 'lever', 'ashby', 'workday', 'adzuna', 'usajobs')"


def upgrade() -> None:
    op.drop_constraint("employers_system", "employers", type_="check")
    op.create_check_constraint("employers_system", "employers", EMPLOYERS_NEW)
    op.drop_constraint("postings_source", "postings", type_="check")
    op.create_check_constraint("postings_source", "postings", POSTINGS_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM postings WHERE source = 'workday'")
    op.execute("DELETE FROM employers WHERE system = 'workday'")
    op.drop_constraint("postings_source", "postings", type_="check")
    op.create_check_constraint("postings_source", "postings", POSTINGS_OLD)
    op.drop_constraint("employers_system", "employers", type_="check")
    op.create_check_constraint("employers_system", "employers", EMPLOYERS_OLD)
