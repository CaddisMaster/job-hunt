"""Rate limiting in Postgres (#2).

A fixed window per key: one row per (key, window), its count bumped with an
upsert. Orbit Ops's limiter lives in process memory, which resets on a restart
and isn't shared between processes; that's fine for one user and wrong for
this app (plan §2).

Each hit commits in its own session, so a count survives whatever the route
does with its own transaction afterwards (a failed login rolls nothing back).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import RateLimit

# Rows older than this are deleted as the key is next used, so the table stays
# the size of the recent traffic.
KEEP = timedelta(days=1)


class RateLimiter:
    def __init__(self, name: str, limit: int, window_seconds: int = 60):
        self.name = name
        self.limit = limit
        self.window = window_seconds

    def _window_start(self, now: datetime) -> datetime:
        epoch = int(now.timestamp())
        return datetime.fromtimestamp(epoch - epoch % self.window, tz=UTC)

    def hit(self, key: str, *, now: datetime | None = None) -> bool:
        """Record an attempt; False if it is over the limit."""
        now = now or datetime.now(UTC)
        full_key = f"{self.name}:{key}"[:200]
        statement = (
            insert(RateLimit)
            .values(key=full_key, window_start=self._window_start(now), hits=1)
            .on_conflict_do_update(index_elements=["key", "window_start"], set_={"hits": RateLimit.hits + 1})
            .returning(RateLimit.hits)
        )
        with SessionLocal() as db:
            hits = db.scalar(statement)
            db.execute(delete(RateLimit).where(RateLimit.key == full_key, RateLimit.window_start < now - KEEP))
            db.commit()
        return hits <= self.limit
