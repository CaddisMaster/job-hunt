"""The bridge (#74): personal access tokens and the JSON API Claude Code calls.

Real tokens, real database, the real app. Each test has its own users and its own
client address (conftest), so tokens, jobs and rate-limit counts never collide.
"""

import hashlib
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Answer, ApiToken, Profile, Resume, SavedJob
from tests.conftest import csrf, fresh_rate_window, login

PROFILE = {
    "roles": [{"title": "Staff Accountant", "employer": "Allegheny Ledger Co.", "start": "Mar 2019", "end": "present", "source": "x"}],  # fmt: skip
    "skills": [{"value": "Excel", "source": "Skills: Excel"}],
    "education": [], "certifications": [], "total_years": 6.0, "seniority": "mid", "target_titles": [],
}  # fmt: skip


def make_token(client, name: str = "Claude Code on my Mac") -> str:
    page = client.post("/profile/tokens", data={"name": name, "csrf_token": csrf(client)}).text
    match = re.search(r'<code class="token">(jh_[A-Za-z0-9_-]+)</code>', page)
    assert match, "the new token wasn't shown"
    return match.group(1)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def me(client, make_user):
    """A logged-in user with a token, a resume, a confirmed profile and a saved job."""
    user = make_user()
    assert login(client, user.email).status_code == 303
    with SessionLocal() as db:
        db.add(Resume(user_id=user.id, filename="resume.docx", kind="docx", size_bytes=10, sha256="0" * 64, content=b"x", text="Jordan Example\nStaff Accountant"))  # fmt: skip
        db.add(Profile(user_id=user.id, data=PROFILE, model="test", reviewed_at=datetime.now(UTC)))
        job = SavedJob(user_id=user.id, url="https://careers.example.com/1", source="page", title="Senior Accountant", company="Acme", location="Pittsburgh, PA", description="Close the books.")  # fmt: skip
        db.add(job)
        db.commit()
        user.job_id = job.id
    user.token = make_token(client)
    return user


# ---- Tokens --------------------------------------------------------------------------


@pytest.mark.criterion(74, "I make an access token and see it once")
def test_a_token_is_shown_once_and_only_its_hash_is_stored(client, me):
    with SessionLocal() as db:
        [row] = db.scalars(select(ApiToken).where(ApiToken.user_id == me.id)).all()
    assert row.token_hash == hashlib.sha256(me.token.encode()).hexdigest()
    assert row.prefix == me.token[:10] and row.name == "Claude Code on my Mac"
    page = client.get("/profile").text
    assert me.token not in page and f"{me.token[:10]}…" in page


def test_a_revoked_token_stops_working(client, me):
    assert client.get("/api/jobs", headers=bearer(me.token)).status_code == 200
    with SessionLocal() as db:
        token_id = db.scalar(select(ApiToken.id).where(ApiToken.user_id == me.id))
    assert client.post(f"/profile/tokens/{token_id}/revoke", data={"csrf_token": csrf(client)}).status_code in (200, 303)
    assert client.get("/api/jobs", headers=bearer(me.token)).status_code == 401
    assert "Revoked" in client.get("/profile").text


def test_someone_elses_token_cant_be_revoked(client, me, make_user):
    other = make_user()
    with SessionLocal() as db:
        db.add(ApiToken(user_id=other.id, name="theirs", prefix="jh_x", token_hash="f" * 64))
        db.commit()
        theirs = db.scalar(select(ApiToken.id).where(ApiToken.user_id == other.id))
    assert client.post(f"/profile/tokens/{theirs}/revoke", data={"csrf_token": csrf(client)}).status_code == 404


def test_a_disabled_accounts_token_stops_working(client, me):
    with SessionLocal() as db:
        db.get(type(me), me.id).disabled_at = datetime.now(UTC)
        db.commit()
    assert client.get("/api/jobs", headers=bearer(me.token)).status_code == 401


# ---- Reading -------------------------------------------------------------------------


@pytest.mark.criterion(74, "My token opens my saved job's package")
def test_my_token_opens_my_saved_jobs_package(client, me):
    listing = client.get("/api/jobs", headers=bearer(me.token)).json()
    assert [job["id"] for job in listing["jobs"]] == [me.job_id]
    package = client.get(f"/api/jobs/{me.job_id}/package", headers=bearer(me.token))
    assert package.status_code == 200 and package.headers["content-type"].startswith("application/json")
    body = package.json()
    assert body["job"]["title"] == "Senior Accountant" and body["job"]["posting"] == "Close the books."
    assert body["resume"]["text"].startswith("Jordan Example")
    assert body["profile"]["roles"][0]["title"] == "Staff Accountant"
    starter = {a["key"]: a for a in body["answers"]["starter"]}
    assert starter["eeo_gender"]["answer"] == "Decline to answer" and starter["salary_expectation"]["answer"] == ""
    with SessionLocal() as db:
        assert db.scalar(select(ApiToken.last_used_at).where(ApiToken.user_id == me.id)) is not None


def test_an_unconfirmed_profile_isnt_handed_over(client, me):
    with SessionLocal() as db:
        db.scalar(select(Profile).where(Profile.user_id == me.id)).reviewed_at = None
        db.commit()
    assert client.get(f"/api/jobs/{me.job_id}/package", headers=bearer(me.token)).json()["profile"] is None


@pytest.mark.criterion(74, "No token, a wrong one or a revoked one is refused")
@pytest.mark.parametrize("header", [None, "Bearer jh_not-a-real-token", "Bearer ", "Basic abc", "jh_bare"])
def test_no_token_or_a_wrong_one_is_refused(client, me, header):
    headers = {"Authorization": header} if header else {}
    response = client.get(f"/api/jobs/{me.job_id}/package", headers=headers)
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"error": "A valid access token is required."}


@pytest.mark.criterion(74, "A token reaches only its owner's data")
def test_a_token_reaches_only_its_owners_data(client, me, make_user):
    other = make_user()
    with SessionLocal() as db:
        theirs = SavedJob(user_id=other.id, url="https://careers.example.com/theirs", source="text", title="Theirs")
        db.add(theirs)
        db.commit()
        theirs_id = theirs.id
    response = client.get(f"/api/jobs/{theirs_id}/package", headers=bearer(me.token))
    assert response.status_code == 404 and response.json() == {"error": "Not found."}
    assert theirs_id not in [job["id"] for job in client.get("/api/jobs", headers=bearer(me.token)).json()["jobs"]]


@pytest.mark.criterion(74, "A session cookie doesn't open the API")
def test_a_session_cookie_doesnt_open_the_api(client, me):
    assert client.get("/profile").status_code == 200  # logged in
    assert client.get("/api/jobs").status_code == 401
    response = client.post("/api/answers", json={"question": "Q?", "answer": "A"})
    assert response.status_code == 401


# ---- Writing answers -------------------------------------------------------------------


@pytest.mark.criterion(74, "An answer given during an application joins my answer bank")
def test_an_answer_to_a_new_question_joins_my_answer_bank(client, me):
    sent = {"question": "How did you  hear about us?", "answer": "A friend at the company"}
    response = client.post("/api/answers", json=sent, headers=bearer(me.token))
    assert response.status_code == 201
    assert response.json()["question"] == "How did you hear about us?"
    # The same question, worded with different case and spacing, updates it.
    client.post("/api/answers", json={"question": "how did you hear about us?", "answer": "Their careers page"}, headers=bearer(me.token))  # fmt: skip
    with SessionLocal() as db:
        rows = db.scalars(select(Answer).where(Answer.user_id == me.id, Answer.question.is_not(None))).all()
    assert [(r.question, r.answer) for r in rows] == [("how did you hear about us?", "Their careers page")]
    assert "Their careers page" in client.get("/profile/answers").text


def test_an_answer_to_a_starter_question_is_checked_like_the_form(client, me):
    bad = client.post("/api/answers", json={"key": "willing_to_relocate", "answer": "Maybe"}, headers=bearer(me.token))
    assert bad.status_code == 422 and bad.json()["fields"] == {"answer": "Choose one of the options."}
    good = client.post("/api/answers", json={"key": "willing_to_relocate", "answer": "No"}, headers=bearer(me.token))
    assert good.status_code == 201 and good.json()["question"] == "Willing to relocate?"
    package = client.get(f"/api/jobs/{me.job_id}/package", headers=bearer(me.token)).json()
    assert {a["key"]: a["answer"] for a in package["answers"]["starter"]}["willing_to_relocate"] == "No"


@pytest.mark.parametrize(
    ("body", "field"),
    [({"answer": "x"}, "question"), ({"question": "Q?", "answer": "  "}, "answer"), ({"key": "nope", "answer": "x"}, "key")],
)
def test_an_answer_without_what_it_needs_is_refused(client, me, body, field):
    response = client.post("/api/answers", json=body, headers=bearer(me.token))
    assert response.status_code == 422 and field in response.json()["fields"]


# ---- Rate limit ------------------------------------------------------------------------


@pytest.mark.criterion(74, "Wrong tokens are rate-limited")
def test_wrong_tokens_are_rate_limited_per_address(client, me):
    fresh_rate_window()
    statuses = [client.get("/api/jobs", headers=bearer("jh_wrong")).status_code for _ in range(22)]
    assert statuses[:20] == [401] * 20 and statuses[20:] == [429, 429]
    # A right token from the same address still works: only failures count.
    assert client.get("/api/jobs", headers=bearer(me.token)).status_code == 200
