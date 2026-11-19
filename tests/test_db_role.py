"""The app's runtime role (migration 0001). Tests connect as the owner, so these
look the role up in the catalog rather than connecting as it."""

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db import SessionLocal


@pytest.mark.criterion(1, "The app connects as a non-superuser role")
def test_the_app_role_exists_and_is_not_a_superuser():
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = 'apply_app'")
        ).one()
    assert tuple(row) == (False, False, False)


def test_the_app_role_cannot_touch_alembic_version():
    with SessionLocal() as db:
        allowed = db.scalar(text("SELECT has_table_privilege('apply_app', 'alembic_version', 'SELECT')"))
    assert allowed is False


def test_the_runtime_url_uses_the_app_role_when_configured():
    settings = Settings(secret_key="x" * 16, db_user="apply", db_app_user="apply_app", db_app_password="pw")
    assert settings.database_url().username == "apply_app"
    assert settings.database_url(owner=True).username == "apply"
