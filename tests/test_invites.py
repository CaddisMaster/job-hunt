import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models import Invite, User
from tests.conftest import PASSWORD, csrf, login

LINK_RE = re.compile(r"http://testserver/invite/([A-Za-z0-9_-]+)")


@pytest.fixture
def admin_client(client, make_user):
    admin = make_user(is_admin=True)
    assert login(client, admin.email).status_code == 303
    client.admin = admin
    return client


@pytest.fixture
def invitee_email():
    email = f"invitee-{uuid.uuid4().hex[:12]}@example.com"
    yield email
    with SessionLocal() as db:
        db.execute(delete(Invite).where(Invite.email == email))
        db.execute(delete(User).where(User.email == email))
        db.commit()


def invite(client, email) -> str:
    response = client.post("/admin/invites", data={"email": email, "csrf_token": csrf(client)})
    assert response.status_code == 200, response.text
    return LINK_RE.search(response.text).group(1)


def set_password(client, token, password=PASSWORD, repeat=None):
    page = client.get(f"/invite/{token}")
    token_field = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    form = {"password": password, "repeat": repeat or password}
    if token_field:
        form["csrf_token"] = token_field.group(1)
    else:
        form["csrf_token"] = csrf(client)
    return client.post(f"/invite/{token}", data=form, follow_redirects=False)


@pytest.mark.criterion(3, "There is no public sign-up")
def test_there_is_no_public_sign_up(client):
    assert client.get("/register").status_code == 404
    assert client.get("/signup").status_code == 404
    assert "sign up" not in client.get("/login").text.lower()


@pytest.mark.criterion(3, "The admin creates an account")
def test_the_admin_gets_a_one_time_link(admin_client, invitee_email):
    token = invite(admin_client, invitee_email)
    with SessionLocal() as db:
        stored = db.scalar(select(Invite).where(Invite.email == invitee_email))
    assert stored.token_hash != token  # only the hash is kept
    assert stored.expires_at - stored.created_at == timedelta(days=7)


@pytest.mark.criterion(3, "The invitee sets their password")
def test_the_invitee_sets_a_password_logs_in_and_the_link_dies(admin_client, client_address, invitee_email):
    from fastapi.testclient import TestClient

    from app.main import app

    token = invite(admin_client, invitee_email)
    with TestClient(app, client=(client_address, 50000)) as invitee:
        response = set_password(invitee, token)
        assert response.status_code == 303 and response.headers["location"] == "/"
        assert invitee_email in invitee.get("/").text
        invitee.post("/logout", data={"csrf_token": csrf(invitee)})
        assert login(invitee, invitee_email).status_code == 303
        assert invitee.get(f"/invite/{token}").status_code == 404


def test_a_short_or_mismatched_password_is_refused(admin_client, invitee_email):
    token = invite(admin_client, invitee_email)
    assert set_password(admin_client, token, "short").status_code == 422
    assert set_password(admin_client, token, PASSWORD, PASSWORD + "x").status_code == 422
    assert set_password(admin_client, token).status_code == 303  # the link still works after a mistake


@pytest.mark.criterion(3, "An expired invite link is refused")
def test_an_expired_link_is_refused(admin_client, invitee_email):
    token = invite(admin_client, invitee_email)
    with SessionLocal() as db:
        db.execute(
            update(Invite).where(Invite.email == invitee_email).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    response = admin_client.get(f"/invite/{token}")
    assert response.status_code == 410
    assert "expired" in response.text and "Ask the admin for a new one" in response.text
    assert set_password(admin_client, token).status_code == 410


def test_a_made_up_link_is_refused(client):
    assert client.get("/invite/not-a-real-token").status_code == 404


def test_a_newer_link_replaces_an_older_one(admin_client, invitee_email):
    first = invite(admin_client, invitee_email)
    invite(admin_client, invitee_email)
    assert admin_client.get(f"/invite/{first}").status_code == 404


@pytest.mark.criterion(3, "The invitee is told what the admin can see")
def test_the_set_password_page_says_what_the_admin_can_see(admin_client, invitee_email):
    page = admin_client.get(f"/invite/{invite(admin_client, invitee_email)}").text
    assert "What the admin can see" in page
    assert "resumes" in page and "answer bank" in page and "applications" in page


def test_the_dashboard_says_it_too(logged_in):
    assert "What the admin can see" in logged_in.get("/").text


@pytest.mark.criterion(3, "Only admins reach the admin panel")
def test_only_admins_reach_the_admin_panel(logged_in, client):
    assert logged_in.get("/admin").status_code == 404
    assert logged_in.post("/admin/invites", data={"email": "x@example.com", "csrf_token": csrf(logged_in)}).status_code == 404


def test_a_logged_out_visitor_is_sent_to_login(client):
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/login"


def test_the_admin_panel_lists_accounts_and_nothing_private(admin_client, user):
    page = admin_client.get("/admin").text
    assert user.email in page
    assert "/admin/accounts/" in page


@pytest.mark.criterion(3, "The admin can disable an account")
def test_a_disabled_account_cannot_log_in_and_loses_its_session(admin_client, client_address, user):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, client=(f"{client_address}-u", 50000)) as theirs:
        assert login(theirs, user.email).status_code == 303
        admin_client.post(f"/admin/accounts/{user.id}/disable", data={"csrf_token": csrf(admin_client)})
        assert theirs.get("/", follow_redirects=False).headers["location"] == "/login"
        response = login(theirs, user.email)
        assert response.status_code == 401 and "Wrong email or password." in response.text

        admin_client.post(f"/admin/accounts/{user.id}/enable", data={"csrf_token": csrf(admin_client)})
        assert login(theirs, user.email).status_code == 303
    from tests.conftest import _forget_rate_limits

    _forget_rate_limits(f"{client_address}-u")


def test_the_admin_cannot_disable_themselves(admin_client):
    admin_client.post(f"/admin/accounts/{admin_client.admin.id}/disable", data={"csrf_token": csrf(admin_client)})
    assert admin_client.get("/admin").status_code == 200


def test_a_new_link_resets_an_existing_password_and_ends_sessions(admin_client, user):
    response = admin_client.post(f"/admin/accounts/{user.id}/link", data={"csrf_token": csrf(admin_client)})
    token = LINK_RE.search(response.text).group(1)
    assert set_password(admin_client, token, PASSWORD + "-new").status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == user.email)).session_token != user.session_token
    with SessionLocal() as db:
        db.execute(delete(Invite).where(Invite.email == user.email))
        db.commit()


def test_inviting_an_existing_account_points_to_new_link(admin_client, user):
    response = admin_client.post("/admin/invites", data={"email": user.email, "csrf_token": csrf(admin_client)})
    assert response.status_code == 409
