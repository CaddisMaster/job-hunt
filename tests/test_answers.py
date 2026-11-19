"""#25: the answer bank. Real forms, real Postgres."""

import pytest
from sqlalchemy import select

from app import answers
from app.db import SessionLocal
from app.models import Answer
from tests.conftest import csrf, login

FILLED = {
    "work_authorized_us": "Yes",
    "needs_sponsorship": "No",
    "salary_expectation": "$70,000–$85,000",
    "start_date": "Two weeks after an offer",
    "willing_to_relocate": "No",
    "remote": "Also show remote jobs",
    "phone": "+1 (412) 555-0100",
    "email": "jordan@example.com",
    "linkedin": "https://www.linkedin.com/in/jordan",
    "github": "",
    "portfolio": "",
    "eeo_gender": answers.DECLINE,
    "eeo_race": answers.DECLINE,
    "eeo_veteran": "I am not a protected veteran",
    "eeo_disability": answers.DECLINE,
}


def save(client, values: dict, **extra):
    return client.post("/profile/answers", data={"csrf_token": csrf(client), **values, **extra}, follow_redirects=False)


def stored(user) -> dict[str, str]:
    with SessionLocal() as db:
        return {a.key: a.answer for a in db.scalars(select(Answer).where(Answer.user_id == user.id))}


@pytest.mark.criterion(25, "I can fill in my answer bank")
def test_i_can_fill_in_my_answer_bank(logged_in, user):
    response = save(logged_in, FILLED)
    assert response.status_code == 303
    assert stored(user) == FILLED
    page = logged_in.get("/profile/answers").text
    assert "Your answers are saved." in page
    assert 'value="$70,000–$85,000"' in page
    assert "<option selected>Also show remote jobs</option>" in page
    assert "<option selected>I am not a protected veteran</option>" in page


@pytest.mark.criterion(25, "EEO answers default to decline")
def test_eeo_answers_default_to_decline(logged_in, user):
    with SessionLocal() as db:
        values = answers.for_user(db, user)
    assert [values[k] for k in ("eeo_gender", "eeo_race", "eeo_veteran", "eeo_disability")] == [answers.DECLINE] * 4
    page = logged_in.get("/profile/answers").text
    assert page.count(f"<option selected>{answers.DECLINE}</option>") == 4


@pytest.mark.criterion(25, "Remote work is off by default")
def test_remote_work_is_off_by_default(logged_in, user):
    with SessionLocal() as db:
        assert answers.for_user(db, user)["remote"] == "Pittsburgh area only"
    assert "<option selected>Pittsburgh area only</option>" in logged_in.get("/profile/answers").text


@pytest.mark.criterion(25, "My answers are mine alone")
def test_my_answers_are_mine_alone(client, make_user):
    me, them = make_user(), make_user()
    login(client, them.email)
    save(client, FILLED)
    client.cookies.clear()
    login(client, me.email)
    page = client.get("/profile/answers").text
    assert "jordan@example.com" not in page and 'value="$70,000' not in page
    # Saving my own answers never touches theirs.
    save(client, {**FILLED, "email": "me@example.com"})
    assert stored(them)["email"] == "jordan@example.com"
    assert stored(me)["email"] == "me@example.com"


@pytest.mark.criterion(25, "An unanswered question stays unanswered")
def test_an_unanswered_question_stays_unanswered(logged_in, user):
    save(logged_in, {**FILLED, "salary_expectation": "", "work_authorized_us": "", "start_date": "   "})
    values = stored(user)
    assert values["salary_expectation"] == "" and values["work_authorized_us"] == "" and values["start_date"] == ""
    page = logged_in.get("/profile/answers").text
    assert '<option value="" selected>(not answered)</option>' in page


def test_nothing_is_prefilled_from_my_account(logged_in, user):
    """The login email isn't copied in as the application email: the user types what applications get."""
    with SessionLocal() as db:
        assert answers.for_user(db, user)["email"] == ""
    assert f'value="{user.email}"' not in logged_in.get("/profile/answers").text


def test_prefer_not_to_say_is_a_salary_answer(logged_in, user):
    save(logged_in, {**FILLED, "salary_expectation": "$90,000"}, salary_prefer_not="yes")
    assert stored(user)["salary_expectation"] == answers.PREFER_NOT
    page = logged_in.get("/profile/answers").text
    assert 'name="salary_prefer_not" value="yes" checked' in page


@pytest.mark.parametrize(
    ("key", "bad", "message"),
    [
        ("email", "not-an-email", "That doesn&#39;t look like an email address."),
        ("phone", "call me maybe", "Use digits, spaces and + ( ) - only."),
        ("linkedin", "javascript:alert(1)", "Start the link with https://."),
        ("needs_sponsorship", "Maybe", "Choose one of the options."),
        ("eeo_gender", "Something else", "Choose one of the options."),
        ("start_date", "x" * 501, "Keep it under 500 characters."),
    ],
)
def test_a_bad_answer_saves_nothing_and_says_why(logged_in, user, key, bad, message):
    save(logged_in, FILLED)
    response = save(logged_in, {**FILLED, key: bad, "phone": "+1 412 555 0199" if key != "phone" else bad})
    assert response.status_code == 400
    assert message in response.text
    assert "Some answers need fixing; nothing was saved." in response.text
    assert stored(user) == FILLED  # all or nothing


def test_answers_need_a_login_and_the_csrf_token(client, logged_in, user):
    assert logged_in.post("/profile/answers", data=FILLED).status_code == 403
    assert stored(user) == {}
    client.cookies.clear()
    assert client.get("/profile/answers", follow_redirects=False).status_code == 303
