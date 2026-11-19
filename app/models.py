"""ORM models.

⚠️ Every table holding a user's private data carries `user_id`, and every
query against it is scoped to the current user (CLAUDE.md non-negotiable #1).
Shared tables (employers, postings) arrive in 0.3."""

from app.db import Base  # noqa: F401  (alembic/env.py imports this module to register tables)
