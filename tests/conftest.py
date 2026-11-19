"""Shared fixtures. The database is a real Postgres (test.sh / CI create a fresh
one and run `alembic upgrade head`); nothing here mocks SQL.

Parallel-safe by construction: every test that needs a row makes its own,
uniquely named, and deletes it afterwards.
"""

import os

# Must be set before app.config is first imported (Settings is cached).
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app, base_url="http://testserver") as c:
        yield c
