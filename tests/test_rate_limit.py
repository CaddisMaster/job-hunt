from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import RateLimit
from app.rate_limit import RateLimiter
from tests.conftest import _forget_rate_limits, unique_address


def test_the_limit_resets_in_the_next_window():
    limiter, key = RateLimiter("test", limit=2), unique_address()
    start = datetime(2026, 11, 18, 12, 0, 5, tzinfo=UTC)
    assert limiter.hit(key, now=start) and limiter.hit(key, now=start)
    assert not limiter.hit(key, now=start + timedelta(seconds=30))
    assert limiter.hit(key, now=start + timedelta(seconds=60))
    _forget_rate_limits(key)


def test_old_windows_are_deleted_as_the_key_is_used():
    limiter, key = RateLimiter("test", limit=5), unique_address()
    limiter.hit(key, now=datetime(2026, 11, 1, tzinfo=UTC))
    limiter.hit(key, now=datetime(2026, 11, 18, tzinfo=UTC))
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).where(RateLimit.key == f"test:{key}")) == 1
    _forget_rate_limits(key)
