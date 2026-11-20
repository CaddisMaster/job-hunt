import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import create_app
from app.models import User
from app.security import login_limiter
from tests.conftest import PASSWORD, csrf, fresh_rate_window, login


@pytest.mark.criterion(2, "A user logs in and out")
def test_a_user_logs_in_reaches_the_dashboard_and_logs_out(client, user):
    response = login(client, user.email)
    assert response.status_code == 303 and response.headers["location"] == "/"
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert user.email in dashboard.text

    client.post("/logout", data={"csrf_token": csrf(client)})
    after = client.get("/", follow_redirects=False)
    assert after.status_code == 303 and after.headers["location"] == "/login"


def test_the_email_is_matched_whatever_its_case(client, user):
    assert login(client, f"  {user.email.upper()} ").status_code == 303


def test_a_login_records_when(client, user):
    login(client, user.email)
    with SessionLocal() as db:
        assert db.scalar(select(User.last_login_at).where(User.id == user.id)) is not None


@pytest.mark.criterion(2, "A wrong password is refused")
def test_a_wrong_password_is_refused_with_a_generic_message(client, user):
    response = login(client, user.email, "not the password")
    assert response.status_code == 401
    assert "Wrong email or password." in response.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_an_unknown_email_gets_the_same_message(client):
    response = login(client, "nobody@example.com")
    assert response.status_code == 401
    assert "Wrong email or password." in response.text


def test_the_dashboard_needs_a_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_htmx_gets_hx_redirect_instead_of_a_redirect(client):
    response = client.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert response.status_code == 401 and response.headers["hx-redirect"] == "/login"


def test_a_rotated_session_token_ends_the_session(logged_in, user):
    with SessionLocal() as db:
        db.get(User, user.id).session_token = "rotated"
        db.commit()
    assert logged_in.get("/", follow_redirects=False).status_code == 303


@pytest.mark.criterion(2, "Repeated failed logins are throttled across restarts")
def test_the_login_limit_survives_a_restart(client_address, user):
    fresh_rate_window()
    with TestClient(create_app(), client=(client_address, 50000)) as first:
        for _ in range(login_limiter.limit):
            assert login(first, user.email, "wrong").status_code == 401
        assert login(first, user.email, "wrong").status_code == 429

    # A fresh app is a restarted process: Orbit Ops's in-memory limiter would forget.
    with TestClient(create_app(), client=(client_address, 50000)) as restarted:
        response = login(restarted, user.email, PASSWORD)
    assert response.status_code == 429
    assert "Too many attempts" in response.text


def test_the_limit_is_per_address(client_address, user):
    for _ in range(login_limiter.limit + 1):
        login_limiter.hit(client_address)
    with TestClient(create_app(), client=(f"{client_address}-other", 50000)) as other:
        assert login(other, user.email).status_code == 303


@pytest.mark.criterion(2, "A POST without a CSRF token is rejected")
def test_a_post_without_a_csrf_token_is_rejected(client, user):
    response = client.post("/login", data={"email": user.email, "password": PASSWORD})
    assert response.status_code == 403
    assert "session expired" in response.text


def test_a_post_with_the_wrong_csrf_token_is_rejected(client, user):
    csrf(client)
    response = client.post("/login", data={"email": user.email, "password": PASSWORD, "csrf_token": "forged"})
    assert response.status_code == 403


def test_htmx_sends_the_csrf_token_as_a_header(logged_in):
    assert "hx-headers='{\"X-CSRF-Token\": \"" in logged_in.get("/").text
