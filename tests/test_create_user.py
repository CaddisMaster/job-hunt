import uuid

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import User
from scripts import create_user
from tests.conftest import PASSWORD, login


@pytest.fixture
def email():
    address = f"Admin-{uuid.uuid4().hex[:12]}@Example.com"
    yield address
    with SessionLocal() as db:
        db.execute(delete(User).where(User.email == address.lower()))
        db.commit()


def _passwords(*answers):
    answers = iter(answers)
    return lambda prompt: next(answers)


@pytest.mark.criterion(2, "The first admin can be created from the command line")
def test_create_user_makes_an_admin_who_can_log_in(client, email):
    assert create_user.main([email, "--admin"], read_password=_passwords(PASSWORD, PASSWORD)) == 0
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
    assert user.is_admin
    assert login(client, email).status_code == 303


def test_create_user_refuses_a_short_password(email):
    assert create_user.main([email], read_password=_passwords("short", "short")) == 1


def test_create_user_refuses_mismatched_passwords(email):
    assert create_user.main([email], read_password=_passwords(PASSWORD, PASSWORD + "x")) == 1


def test_resetting_a_password_ends_existing_sessions(email):
    create_user.main([email], read_password=_passwords(PASSWORD, PASSWORD))
    with SessionLocal() as db:
        before = db.scalar(select(User.session_token).where(User.email == email.lower()))
    create_user.main([email], read_password=_passwords(PASSWORD + "2", PASSWORD + "2"))
    with SessionLocal() as db:
        assert db.scalar(select(User.session_token).where(User.email == email.lower())) != before
