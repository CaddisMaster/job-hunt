"""The answer bank (#25): what applications ask that a resume doesn't say (plan §3).

The starter questions are defined here, each with the only default it may have.
Most have none: blank stays blank, and blank means "ask me" when an application
needs it (non-negotiable 7). Two have defaults on purpose:
- the EEO questions default to "Decline to answer", as they are voluntary;
- remote work defaults to off: the Pittsburgh area only (plan §10, decision 4).

Nothing here is filled from the resume or the profile. Contact details on a
resume are not silently copied: the user types what applications should get.

⚠️ Every function takes the current user and touches only their rows
(non-negotiable 1).
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Answer, User

DECLINE = "Decline to answer"
PREFER_NOT = "Prefer not to say"
MAX_ANSWER = 500


@dataclass(frozen=True)
class Question:
    key: str
    label: str
    kind: str = "text"  # text, choice, email, tel, url
    choices: tuple[str, ...] = ()
    default: str = ""
    hint: str = ""
    group: str = "work"
    placeholder: str = ""


_YES_NO = ("Yes", "No")

QUESTIONS: tuple[Question, ...] = (
    Question("work_authorized_us", "Are you legally authorized to work in the United States?", "choice", _YES_NO),
    Question("needs_sponsorship", "Will you now or in the future need visa sponsorship?", "choice", _YES_NO),
    Question(
        "salary_expectation", "Salary expectation", placeholder="e.g. $70,000–$85,000",
        hint=f"A range is usual. Leave it blank to be asked each time, or choose “{PREFER_NOT}”.",
    ),
    Question("start_date", "Earliest start date", placeholder="e.g. Two weeks after an offer"),
    Question("willing_to_relocate", "Willing to relocate?", "choice", _YES_NO),
    Question(
        "remote", "Remote jobs", "choice", ("Pittsburgh area only", "Also show remote jobs"),
        default="Pittsburgh area only", hint="Off by default: only jobs in the Pittsburgh area.",
    ),
    Question("phone", "Phone for applications", "tel", group="contact"),
    Question("email", "Email for applications", "email", group="contact",
             hint="Can differ from the one you log in with."),
    Question("linkedin", "LinkedIn profile", "url", group="contact", placeholder="https://www.linkedin.com/in/…"),
    Question("github", "GitHub", "url", group="contact", placeholder="https://github.com/…"),
    Question("portfolio", "Portfolio or website", "url", group="contact", placeholder="https://…"),
    Question("eeo_gender", "Gender", "choice", ("Female", "Male", "Non-binary", DECLINE), DECLINE, group="eeo"),
    Question(
        "eeo_race", "Race / ethnicity", "choice",
        (
            "American Indian or Alaska Native", "Asian", "Black or African American", "Hispanic or Latino",
            "Native Hawaiian or Other Pacific Islander", "White", "Two or more races", DECLINE,
        ),
        DECLINE, group="eeo",
    ),
    Question("eeo_veteran", "Veteran status", "choice", ("I am a protected veteran", "I am not a protected veteran", DECLINE),
             DECLINE, group="eeo"),
    Question("eeo_disability", "Disability status", "choice", ("Yes, I have a disability", "No, I don't have a disability", DECLINE),
             DECLINE, group="eeo"),
)
BY_KEY = {q.key: q for q in QUESTIONS}
GROUPS = (("work", "Work"), ("contact", "Contact details for applications"), ("eeo", "Voluntary self-identification (EEO)"))

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TEL = re.compile(r"^[0-9+().\-\s]{7,30}$")


class Invalid(ValueError):
    """Answers that weren't saved; `errors` maps a question key to what's wrong."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("invalid answers")
        self.errors = errors


def _check(question: Question, value: str) -> str | None:
    """What's wrong with this answer, or None. Blank is always allowed: it means unanswered."""
    if not value:
        return None
    if len(value) > MAX_ANSWER:
        return f"Keep it under {MAX_ANSWER} characters."
    if question.kind == "choice" and value not in question.choices:
        return "Choose one of the options."
    if question.kind == "email" and not _EMAIL.match(value):
        return "That doesn't look like an email address."
    if question.kind == "tel" and not _TEL.match(value):
        return "Use digits, spaces and + ( ) - only."
    if question.kind == "url" and not value.startswith(("https://", "http://")):
        return "Start the link with https://."
    return None


def for_user(db: Session, user: User) -> dict[str, str]:
    """The user's answer to every starter question: their own where they have one,
    else the question's default (blank for most)."""
    stored = {a.key: a.answer for a in db.scalars(select(Answer).where(Answer.user_id == user.id))}
    return {q.key: stored.get(q.key, q.default) for q in QUESTIONS}


def added_questions(db: Session, user: User) -> list[Answer]:
    """Questions beyond the starter set, added once their owner approved an answer (0.5)."""
    query = select(Answer).where(Answer.user_id == user.id, Answer.question.is_not(None)).order_by(Answer.id)
    return list(db.scalars(query))


def save(db: Session, user: User, form: dict[str, str]) -> None:
    """Save the starter answers from the form, all or nothing. A blank answer is
    stored as blank, never replaced with a guess or a default."""
    values = {}
    errors = {}
    for question in QUESTIONS:
        value = (form.get(question.key) or "").strip()
        if question.key == "salary_expectation" and form.get("salary_prefer_not") == "yes":
            value = PREFER_NOT
        if (problem := _check(question, value)) is not None:
            errors[question.key] = problem
        values[question.key] = value
    if errors:
        raise Invalid(errors)
    existing = {a.key: a for a in db.scalars(select(Answer).where(Answer.user_id == user.id))}
    for key, value in values.items():
        row = existing.get(key) or Answer(user_id=user.id, key=key)
        row.answer = value
        db.add(row)
    db.commit()


QUESTION_MAX = 500


def _added_key(question: str) -> str:
    """A stable key for a question beyond the starter set: the same wording (case,
    spacing and punctuation aside) is the same question."""
    import hashlib

    from app.grounding import normalize

    return "q-" + hashlib.sha256(normalize(question).encode()).hexdigest()[:24]


def add(db: Session, user: User, *, answer: str, key: str | None = None, question: str | None = None) -> Answer:
    """Record one answer the user gave (uncommitted): to a starter question by `key`,
    or to a new question by its wording, checked as the answers page checks it.
    Invalid on a bad answer or no question."""
    value = (answer or "").strip()
    if key in BY_KEY:
        starter, text = BY_KEY[key], None
    elif key:
        raise Invalid({"key": "There's no starter question with that key."})
    else:
        text = " ".join((question or "").split())
        if not text:
            raise Invalid({"question": "Say what the question was."})
        if len(text) > QUESTION_MAX:
            raise Invalid({"question": f"Keep the question under {QUESTION_MAX} characters."})
        starter, key = Question(key="", label=text), _added_key(text)
    if not value:
        raise Invalid({"answer": "An empty answer isn't saved: blank already means \"ask me\"."})
    if (problem := _check(starter, value)) is not None:
        raise Invalid({"answer": problem})
    row = db.scalar(select(Answer).where(Answer.user_id == user.id, Answer.key == key))
    row = row or Answer(user_id=user.id, key=key)
    row.question, row.answer = text, value
    db.add(row)
    db.flush()
    return row
