"""#26: the truthfulness rule's eval set. One fixed profile and answer bank, and a
table of claims with the verdict each must get, including the tricky ones: a skill
from a nearby family, inflated years, a degree not held, a salary when the answer is
"prefer not to say", and questions only the user can answer.

Adding a case is the way to pin a behaviour: if drafting (0.5) ever needs a claim
judged differently, the change starts here."""

from datetime import UTC, date, datetime

import pytest

from app import answers, grounding
from app.db import SessionLocal
from app.grounding import Verdict
from app.models import Profile

S, U, N = Verdict.SUPPORTED, Verdict.UNSUPPORTED, Verdict.NEEDS_USER
TODAY = date(2026, 11, 19)

PROFILE = {
    "roles": [
        {"title": "Staff Accountant", "employer": "Allegheny Ledger Co.", "start": "Nov 2023", "end": "present", "source": "…"},
        {"title": "Accounting Assistant", "employer": "Three Rivers Books", "start": "2016", "end": "2019", "source": "…"},
        {"title": "Bookkeeper", "employer": "Corner Shop", "start": "", "end": "", "source": "…"},
    ],
    "skills": [
        {"value": "SQL", "source": "…"},
        {"value": "Excel", "source": "…"},
        {"value": "Account reconciliation", "source": "…"},
        {"value": "Node.js", "source": "…"},
    ],
    "education": [{"value": "B.S. Accounting", "source": "…"}],
    "certifications": [{"value": "CPA", "source": "…"}],
    "total_years": 6.0,
    "seniority": "mid",
    "target_titles": ["Staff Accountant", "Senior Accountant"],
}
ANSWERS = {
    **{q.key: q.default for q in answers.QUESTIONS},
    "work_authorized_us": "Yes",
    "needs_sponsorship": "No",
    "salary_expectation": answers.PREFER_NOT,
    "start_date": "",  # left blank: ask me
    "email": "jordan@example.com",
}
FACTS = grounding.Facts(profile=PROFILE, answers=ANSWERS)

CASES = [
    # (what, claim, expected, why)
    ("skill", "SQL", S, "listed"),
    ("skill", "sql", S, "case doesn't matter"),
    ("skill", "Account  Reconciliation", S, "whitespace doesn't matter"),
    ("skill", "NodeJS", S, "punctuation inside a name doesn't matter"),
    ("skill", "PostgreSQL", U, "a nearby family isn't the skill"),
    ("skill", "MySQL", U, "nor is another member of it"),
    ("skill", "Python", U, "not listed"),
    ("skill", "Excel macros", U, "more than what's listed"),
    ("title", "Staff Accountant", S, "held"),
    ("title", "Senior Accountant", U, "a target title isn't a title held"),
    ("title", "Accountant", U, "a shorter title isn't the title held"),
    ("degree", "B.S. Accounting", S, "held"),
    ("degree", "BS Accounting", S, "the same degree, punctuated differently"),
    ("degree", "MBA", U, "a degree not held"),
    ("degree", "B.S. Finance", U, "a different degree"),
    ("certification", "CPA", S, "held"),
    ("certification", "CMA", U, "not held"),
]


@pytest.mark.parametrize(("what", "claim", "expected", "why"), CASES, ids=[f"{c[0]}:{c[1]}" for c in CASES])
def test_the_eval_set(what, claim, expected, why):
    assert getattr(grounding, what)(FACTS, claim) == expected, why


YEARS = [
    # (claimed, in title, expected, why)
    (6, None, S, "exactly what the profile says"),
    (5, None, S, "less than the profile says"),
    (7, None, U, "more than the profile says"),
    (6.5, None, U, "never rounded up"),
    (3, "Staff Accountant", S, "Nov 2023 to now is 3 years"),
    (5, "Staff Accountant", U, "3 years in the title, 5 claimed"),
    (3, "Accounting Assistant", S, "2016 to 2019"),
    (4, "Accounting Assistant", U, "more than 2016 to 2019"),
    (1, "Bookkeeper", N, "no dates on the role: only the user knows"),
    (1, "Controller", U, "a title never held"),
]


@pytest.mark.parametrize(("claimed", "as_title", "expected", "why"), YEARS, ids=[f"{y[0]}y:{y[1]}" for y in YEARS])
def test_years_are_never_inflated(claimed, as_title, expected, why):
    assert grounding.years(FACTS, claimed, as_title, today=TODAY) == expected, why


def test_years_need_the_user_when_the_profile_has_none():
    facts = grounding.Facts(profile={**PROFILE, "total_years": None}, answers=ANSWERS)
    assert grounding.years(facts, 1, today=TODAY) == N


ANSWER_CASES = [
    # (key, stated, expected, why)
    ("work_authorized_us", None, S, "answered"),
    ("work_authorized_us", "Yes", S, "stated as answered"),
    ("work_authorized_us", "No", U, "stated against the answer"),
    ("start_date", None, N, "left blank: ask, never guess"),
    ("start_date", "Immediately", N, "a blank can't support any statement"),
    ("salary_expectation", None, S, "'prefer not to say' is an answer"),
    ("salary_expectation", answers.PREFER_NOT, S, "and may be stated"),
    ("salary_expectation", "$75,000", U, "but no salary may be stated in its place"),
    ("eeo_gender", None, S, "the EEO default is an answer"),
    ("eeo_gender", "Female", U, "and nothing else may be stated for it"),
    ("email", "Jordan@Example.com", S, "case doesn't matter"),
    ("phone", None, N, "never filled in"),
    ("years_of_kubernetes", None, N, "a question the bank doesn't have goes to the user"),
]


@pytest.mark.parametrize(("key", "stated", "expected", "why"), ANSWER_CASES, ids=[f"{a[0]}:{a[1]}" for a in ANSWER_CASES])
def test_answers(key, stated, expected, why):
    assert grounding.answer(FACTS, key, stated) == expected, why


# --- the five scenarios ---------------------------------------------------------
@pytest.mark.criterion(26, "A supported claim passes")
def test_a_supported_claim_passes():
    assert grounding.skill(FACTS, "Excel") == S


@pytest.mark.criterion(26, "An unsupported claim is caught")
def test_an_unsupported_claim_is_caught():
    assert grounding.skill(FACTS, "Kubernetes") == U


@pytest.mark.criterion(26, "Inflated experience is caught")
def test_inflated_experience_is_caught():
    assert grounding.years(FACTS, 5, "Staff Accountant", today=TODAY) == U


@pytest.mark.criterion(26, "A question the bank can't answer goes to the user")
def test_a_question_the_bank_cant_answer_goes_to_the_user():
    assert grounding.answer(FACTS, "start_date") == N
    assert grounding.answer(FACTS, "relocation_package_needed") == N


@pytest.mark.criterion(26, '"Prefer not to say" is respected')
def test_prefer_not_to_say_is_respected():
    assert grounding.answer(FACTS, "salary_expectation", "$80,000") == U


# --- facts come only from a confirmed profile -----------------------------------
def test_nothing_can_be_claimed_before_the_profile_is_confirmed(make_user):
    user = make_user()
    with SessionLocal() as db:
        assert grounding.facts(db, user) is None  # no profile
        db.add(Profile(user_id=user.id, data=PROFILE, model="claude-opus-5-5"))
        db.commit()
        assert grounding.facts(db, user) is None  # built, not confirmed
        profile = db.query(Profile).filter_by(user_id=user.id).one()
        profile.reviewed_at = datetime.now(UTC)
        db.commit()
        facts = grounding.facts(db, user)
    assert facts is not None and grounding.skill(facts, "SQL") == S
    assert facts.answers["eeo_gender"] == answers.DECLINE  # the bank's defaults come with it


def test_in_source_needs_the_value_in_its_line_and_the_line_in_the_document():
    doc = grounding.normalize("Skills: SQL, Excel\nCPA (Pennsylvania), 2020")
    assert grounding.in_source("Excel", "skills: sql, excel", doc)
    assert not grounding.in_source("Python", "Skills: SQL, Excel", doc)  # not in its line
    assert not grounding.in_source("SAP", "Skills: SAP", doc)  # line not in the document
    assert not grounding.in_source("", "Skills: SQL, Excel", doc) and not grounding.in_source("SQL", "", doc)
