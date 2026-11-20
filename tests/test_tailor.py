"""#73: a tailored resume sent by Claude Code is checked, made into a PDF and stored.

Real tokens, the real app and a real PDF, read back with pypdf to prove what's on
it and what isn't. Each test has its own users, so nothing is shared.
"""

import io
from datetime import UTC, datetime

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app import tailor
from app.db import SessionLocal
from app.models import Profile, Resume, SavedJob, TailoredResume
from tests.conftest import csrf, login
from tests.test_api import bearer, make_token

RESUME = """Jordan Example
jordan@example.com | (412) 555-0100 | Pittsburgh, PA
Summary
Staff accountant with six years of month-end close experience. Known for clean audits.
Staff Accountant, Allegheny Ledger Co. — Mar 2019 – present
• Led month-end close for 14 entities
• Reconciled 40 balance-sheet accounts monthly
Accounting Assistant, Three Rivers Books, 2016 – 2019
• Processed 300 invoices a week
• Prepared audit schedules
Skills: account reconciliation, month-end close, Excel
B.S. Accounting, University of Pittsburgh, 2016"""

PROFILE = {
    "roles": [
        {"title": "Staff Accountant", "employer": "Allegheny Ledger Co.", "start": "Mar 2019", "end": "present",
         "source": "Staff Accountant, Allegheny Ledger Co. — Mar 2019 – present"},
        {"title": "Accounting Assistant", "employer": "Three Rivers Books", "start": "2016", "end": "2019",
         "source": "Accounting Assistant, Three Rivers Books, 2016 – 2019"},
    ],
    "skills": [{"value": "month-end close", "source": "Skills: …"}, {"value": "Excel", "source": "Skills: …"},
               {"value": "account reconciliation", "source": "Skills: …"}],
    "education": [{"value": "B.S. Accounting", "source": "B.S. Accounting, University of Pittsburgh, 2016"}],
    "certifications": [], "total_years": 9.5, "seniority": "mid", "target_titles": [],
}  # fmt: skip

SENT = {
    "name": "Jordan Example",
    "contact": ["jordan@example.com", "(412) 555-0100", "Pittsburgh, PA"],
    "summary": ["Known for clean audits."],
    "roles": [
        {"role_number": 1, "bullets": ["Reconciled 40 balance-sheet accounts monthly", "• Led month-end close for 14 entities"]},
        {"role_number": 2, "bullets": ["Prepared audit schedules"]},
    ],
    "skills": ["account reconciliation", "Excel"],
    "education": ["B.S. Accounting"],
}


@pytest.fixture
def me(client, make_user):
    user = make_user()
    assert login(client, user.email).status_code == 303
    with SessionLocal() as db:
        db.add(Resume(user_id=user.id, filename="resume.docx", kind="docx", size_bytes=10, sha256="0" * 64, content=b"x", text=RESUME))  # fmt: skip
        db.add(Profile(user_id=user.id, data=PROFILE, model="test", reviewed_at=datetime.now(UTC)))
        job = SavedJob(user_id=user.id, url="https://careers.example.com/sa", source="page", title="Senior Accountant", company="Acme", description="Reconciliations.")  # fmt: skip
        pending = SavedJob(user_id=user.id, url="https://careers.example.com/wait", source="pending")
        db.add_all([job, pending])
        db.commit()
        user.job_id, user.pending_id = job.id, pending.id
    user.token = make_token(client)
    return user


def send(client, user, body=None, job_id=None):
    return client.post(f"/api/jobs/{job_id or user.job_id}/tailored", json=body or SENT, headers=bearer(user.token))


def pdf_text(data: bytes) -> str:
    return " ".join(" ".join(page.extract_text().split()) for page in PdfReader(io.BytesIO(data)).pages)


def stored(user) -> TailoredResume | None:
    with SessionLocal() as db:
        row = db.scalar(select(TailoredResume).where(TailoredResume.saved_job_id == user.job_id))
        if row is not None:
            db.expunge(row)
        return row


# ---- Checked, stored, made into a PDF ------------------------------------------------------


@pytest.mark.criterion(73, "A tailored resume is checked, stored and made into a PDF")
def test_a_tailored_resume_is_checked_stored_and_made_into_a_pdf(client, me):
    response = send(client, me)
    assert response.status_code == 201
    body = response.json()
    assert body["approved"] is False and body["left_out"] == []
    assert body["pdf_filename"] == "Jordan Example - Resume.pdf"
    assert [r["bullets"] for r in body["content"]["roles"]] == [
        ["Reconciled 40 balance-sheet accounts monthly", "Led month-end close for 14 entities"],
        ["Prepared audit schedules"],
    ]
    pdf = client.get(f"/api/jobs/{me.job_id}/resume.pdf", headers=bearer(me.token))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["x-approved"] == "false" and pdf.headers["cache-control"] == "private, no-store"
    text = pdf_text(pdf.content)
    for expected in ("Jordan Example", "Staff Accountant, Allegheny Ledger Co.", "Mar 2019 – present",
                     "Reconciled 40 balance-sheet accounts monthly", "account reconciliation, Excel", "B.S. Accounting"):  # fmt: skip
        assert expected in text
    assert text.index("Reconciled 40") < text.index("Led month-end close")  # the order chosen
    assert "Processed 300 invoices" not in text  # not chosen
    assert stored(me).model == "claude-code"


@pytest.mark.criterion(73, "Nothing unsupported reaches the PDF")
def test_nothing_unsupported_reaches_the_pdf(client, me):
    body = {
        **SENT,
        "summary": ["Known for clean audits.", "A strategic finance leader who transformed operations."],
        "roles": [{"role_number": 1, "bullets": ["Led month-end close for 14 entities", "Managed a team of 12 accountants"]}],
        "skills": ["Excel", "SAP", "Leadership"],
        "education": ["B.S. Accounting", "MBA"],
        "contact": ["jordan@example.com", "linkedin.com/in/someone-else"],
    }
    left_out = send(client, me, body).json()["left_out"]
    assert {(i["section"], i["text"]) for i in left_out} == {
        ("summary", "A strategic finance leader who transformed operations."),
        ("roles", "Managed a team of 12 accountants"),
        ("skills", "SAP"), ("skills", "Leadership"), ("education", "MBA"),
        ("contact", "linkedin.com/in/someone-else"),
    }  # fmt: skip
    text = pdf_text(stored(me).pdf)
    for invented in ("strategic finance leader", "team of 12", "SAP", "Leadership", "MBA", "someone-else"):
        assert invented not in text
    page = client.get(f"/saved/{me.job_id}").text
    assert "Left out" in page and "Managed a team of 12 accountants" in page and "not in your resume" in page


@pytest.mark.criterion(73, "A bullet stays under its own job")
def test_a_bullet_stays_under_its_own_job(client, me):
    body = {**SENT, "roles": [{"role_number": 1, "bullets": ["Processed 300 invoices a week", "Led month-end close for 14 entities"]}]}
    response = send(client, me, body).json()
    assert response["content"]["roles"][0]["bullets"] == ["Led month-end close for 14 entities"]
    assert response["left_out"] == [{"section": "roles", "text": "Processed 300 invoices a week", "why": "in your resume, but not under “Staff Accountant”"}]  # fmt: skip


@pytest.mark.criterion(73, "Roles and dates come from my profile")
def test_roles_and_dates_come_from_my_profile(client, me):
    body = {**SENT, "roles": [{"role_number": 1, "title": "Chief Financial Officer", "start": "2010", "bullets": []}, {"role_number": 9, "bullets": ["x"]}]}  # fmt: skip
    response = send(client, me, body).json()
    roles = response["content"]["roles"]
    assert [(r["title"], r["start"], r["end"]) for r in roles] == [("Staff Accountant", "Mar 2019", "present"), ("Accounting Assistant", "2016", "2019")]  # fmt: skip
    assert {"section": "roles", "text": "role 9", "why": "there's no such role in your confirmed profile"} in response["left_out"]
    text = pdf_text(stored(me).pdf)
    assert "Chief Financial Officer" not in text and "2010" not in text and "Accounting Assistant, Three Rivers Books" in text


def test_a_name_or_contact_not_in_the_resume_is_left_out():
    checked = tailor.check({"name": "Someone Else", "contact": ["(412) 555-0100"]}, PROFILE, RESUME)
    assert checked.content["name"] == "" and checked.content["contact"] == ["(412) 555-0100"]
    assert checked.left_out == [{"section": "name", "text": "Someone Else", "why": "not in your resume"}]


def test_bullets_of_a_role_typed_in_by_hand_cant_be_checked():
    profile = {**PROFILE, "roles": [{**PROFILE["roles"][0], "source": "added or changed by you"}]}
    checked = tailor.check({"roles": [{"role_number": 1, "bullets": ["Led month-end close for 14 entities"]}]}, profile, RESUME)
    assert checked.content["roles"][0]["bullets"] == [] and "couldn't be found" in checked.left_out[0]["why"]


# ---- Approval ------------------------------------------------------------------------------


@pytest.mark.criterion(73, "I approve it before it's used")
def test_it_isnt_used_until_approved_and_a_new_one_clears_the_approval(client, me):
    send(client, me)

    def ready() -> bool:
        jobs = client.get("/api/jobs", headers=bearer(me.token)).json()["jobs"]
        return next(j for j in jobs if j["id"] == me.job_id)["ready_to_apply"]

    assert not ready()
    assert client.post(f"/api/jobs/{me.job_id}/tailored/approve", headers=bearer(me.token)).json()["approved"] is True
    assert ready()
    assert client.get(f"/api/jobs/{me.job_id}/resume.pdf", headers=bearer(me.token)).headers["x-approved"] == "true"
    send(client, me, {**SENT, "skills": ["Excel"]})
    assert not ready() and stored(me).approved_at is None
    # Approving on the saved job's page works the same way.
    assert "Approve this resume" in client.get(f"/saved/{me.job_id}").text
    response = client.post(f"/saved/{me.job_id}/resume/approve", data={"csrf_token": csrf(client)}, follow_redirects=False)
    assert response.status_code == 303 and ready()
    page = client.get(f"/saved/{me.job_id}").text
    assert "this is the resume used to apply" in page and "Approve this resume" not in page


def test_the_pdf_downloads_from_the_saved_job_page(client, me):
    send(client, me)
    response = client.get(f"/saved/{me.job_id}/resume.pdf")
    assert response.status_code == 200 and "Jordan Example" in pdf_text(response.content)
    assert 'filename="Jordan Example - Resume.pdf"' in response.headers["content-disposition"]


# ---- Not ready, and private --------------------------------------------------------------------


def test_a_job_waiting_for_its_text_cant_be_tailored(client, me):
    response = send(client, me, job_id=me.pending_id)
    assert response.status_code == 409 and "waiting for its posting text" in response.json()["error"]


def test_an_unconfirmed_profile_cant_be_tailored_against(client, me):
    with SessionLocal() as db:
        db.scalar(select(Profile).where(Profile.user_id == me.id)).reviewed_at = None
        db.commit()
    response = send(client, me)
    assert response.status_code == 409 and "Confirm your profile first" in response.json()["error"]


@pytest.mark.criterion(73, "A tailored resume is mine alone")
def test_a_tailored_resume_is_mine_alone(client, me, make_user):
    send(client, me)
    other = make_user()
    with SessionLocal() as db:
        theirs = SavedJob(user_id=other.id, url="https://careers.example.com/theirs", source="text", title="Theirs")
        db.add(theirs)
        db.commit()
        theirs_id = theirs.id
    # My token on their job, and their job through my session: both 404.
    assert send(client, me, job_id=theirs_id).status_code == 404
    assert client.get(f"/api/jobs/{theirs_id}/resume.pdf", headers=bearer(me.token)).status_code == 404
    assert client.get(f"/saved/{theirs_id}/resume.pdf").status_code == 404
    # And someone else's session can't get mine.
    client.cookies.clear()
    assert login(client, other.email).status_code == 303
    assert client.get(f"/saved/{me.job_id}/resume.pdf").status_code == 404
    assert client.post(f"/saved/{me.job_id}/resume/approve", data={"csrf_token": csrf(client)}).status_code == 404


# ---- The PDF itself --------------------------------------------------------------------------


def test_the_same_content_gives_the_same_pdf():
    content = tailor.check(SENT, PROFILE, RESUME).content
    assert tailor.render(content) == tailor.render(content)


def test_characters_helvetica_cant_draw_lose_their_accents_not_their_letters():
    # Windows-1252 has ë, ü and ó; ł, ś and ń aren't in it.
    assert tailor._latin("Zoë Müller, Kraków – “quoted”") == "Zoë Müller, Kraków – “quoted”"
    assert tailor._latin("Łódź, Gdańsk, Śląsk") == "Lódz, Gdansk, Slask"
    assert tailor._latin("日本") == "??"
    content = {"name": "Łukasz <b>Nowak</b> & Co", "roles": [], "skills": []}
    text = pdf_text(tailor.render(content))
    assert "Lukasz <b>Nowak</b> & Co" in text
