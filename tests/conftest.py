"""Shared fixtures. The database is a real Postgres (test.sh / CI create a fresh
one and run `alembic upgrade head`); nothing here mocks SQL.

Parallel-safe by construction: every test that needs a user makes its own,
with a unique email, and deletes it afterwards. Every client gets its own
made-up address too, because the login limiter counts per address in Postgres
and xdist workers share the database.
"""

import os
import re
import time
import uuid

# Must be set before app.config is first imported (Settings is cached).
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import security
from app.db import SessionLocal
from app.main import app
from app.models import RateLimit, User
from app.security import hash_password

# argon2's production parameters spend 64 MiB per hash. Parallel workers doing
# that at once blow through the container's memory, and no test is about hash
# strength (Orbit Ops's same trade).
security._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
security._DUMMY_HASH = security._hasher.hash("dummy")

PASSWORD = "correct horse battery staple"
_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def unique_address() -> str:
    return f"test-{uuid.uuid4().hex[:16]}"


def fresh_rate_window(window_seconds: int = 60, need_seconds: float = 15) -> None:
    """Wait, if need be, until a fixed rate-limit window has `need_seconds` left.
    Windows are aligned to the clock (app/rate_limit.py), so a burst of requests that
    straddles a minute boundary is counted in two windows and a limit test flakes."""
    left = window_seconds - time.time() % window_seconds
    if left < need_seconds:
        time.sleep(left + 0.05)


def _forget_rate_limits(address: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(RateLimit).where(RateLimit.key.like(f"%:{address}")))
        db.commit()


@pytest.fixture
def client_address() -> str:
    address = unique_address()
    yield address
    _forget_rate_limits(address)


@pytest.fixture
def client(client_address):
    with TestClient(app, base_url="http://testserver", client=(client_address, 50000)) as c:
        yield c


@pytest.fixture
def make_user():
    created: list[int] = []

    def _make(password: str = PASSWORD, *, is_admin: bool = False) -> User:
        with SessionLocal() as db:
            user = User(
                email=f"user-{uuid.uuid4().hex[:12]}@example.com",
                password_hash=hash_password(password),
                is_admin=is_admin,
            )
            db.add(user)
            db.commit()
            created.append(user.id)
            return user

    yield _make
    with SessionLocal() as db:
        db.execute(delete(User).where(User.id.in_(created)))
        db.commit()


def csrf(client: TestClient) -> str:
    """The CSRF token of the client's current session, read the way a browser
    gets it: from a rendered form."""
    match = _CSRF_RE.search(client.get("/login").text)
    assert match, "login page did not render a csrf_token field"
    return match.group(1)


def login(client: TestClient, email: str, password: str = PASSWORD, follow_redirects: bool = False):
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf(client)},
        follow_redirects=follow_redirects,
    )


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture
def logged_in(client, user):
    response = login(client, user.email)
    assert response.status_code == 303
    return client
