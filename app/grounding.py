"""The truthfulness rule in code (#26, non-negotiable 7).

Given a claim something wants to make on a user's behalf (a skill, a title held,
years of experience, a degree, an answer to a question) and that user's
*confirmed* profile and answer bank, say whether the claim is:

- SUPPORTED: the profile or answer bank says so;
- UNSUPPORTED: it doesn't, so the claim must not be made;
- NEEDS_USER: only the user can say, so ask them, never guess.

Deterministic on purpose: no model call, exact matches after normalising case,
Unicode, dashes, quotes and whitespace. A skill "in the same family" is not the
skill (SQL is not PostgreSQL), and years are compared as numbers, never rounded
up. Being too strict costs the user a question; being too loose puts words in
their mouth, and only the first is acceptable.

Profile building (#24) uses `normalize` and `in_source` to drop extracted items
whose quote isn't in the resume. Drafting (0.5) will check every claim it writes
here before anything is shown.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy.orm import Session

from app import answers as answer_bank
from app.config import get_settings
from app.models import Profile, User


class Verdict(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    NEEDS_USER = "needs_user"


_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def normalize(text: str) -> str:
    """For comparing text: case, Unicode forms, dashes, quotes and runs of whitespace
    (PDF extraction breaks lines and spaces unpredictably) don't count."""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES).casefold()
    return re.sub(r"\s+", " ", text).strip()


def in_source(value: str, source: str, document: str) -> bool:
    """`value` is in its quoted `source`, and the quote is in `document` (all normalised;
    `document` is passed already normalised, as it's checked many times)."""
    value, source = normalize(value), normalize(source)
    return bool(value) and bool(source) and source in document and value in source


def _key(text: str) -> str:
    """For matching one named thing against another: also ignores punctuation at the
    edges and inside (so "B.S." matches "BS", "Node.js" matches "nodejs")."""
    return re.sub(r"[^\w+#]", "", normalize(text))


@dataclass(frozen=True)
class Facts:
    """What a user has confirmed: the profile they reviewed and their answer bank."""

    profile: dict
    answers: dict[str, str]

    def _values(self, field: str) -> set[str]:
        return {_key(item["value"]) for item in self.profile.get(field, [])}


def facts(db: Session, user: User) -> Facts | None:
    """The user's confirmed facts, or None while their profile is missing or unconfirmed:
    then nothing can be claimed for them at all."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).one_or_none()
    if profile is None or profile.reviewed_at is None:
        return None
    return Facts(profile=profile.data, answers=answer_bank.for_user(db, user))


def skill(facts: Facts, name: str) -> Verdict:
    return Verdict.SUPPORTED if _key(name) in facts._values("skills") else Verdict.UNSUPPORTED


def degree(facts: Facts, name: str) -> Verdict:
    return Verdict.SUPPORTED if _key(name) in facts._values("education") else Verdict.UNSUPPORTED


def certification(facts: Facts, name: str) -> Verdict:
    return Verdict.SUPPORTED if _key(name) in facts._values("certifications") else Verdict.UNSUPPORTED


def title(facts: Facts, name: str) -> Verdict:
    """A title the user has held, as their profile words it."""
    held = {_key(role["title"]) for role in facts.profile.get("roles", [])}
    return Verdict.SUPPORTED if _key(name) in held else Verdict.UNSUPPORTED


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _when(text: str, today: date) -> date | None:
    """A role's start or end as written: "Mar 2019", "03/2019", "2019" or "present"."""
    text = normalize(text)
    if text in ("present", "current", "now", "today"):
        return today
    if m := re.fullmatch(r"([a-z]{3})[a-z]*\.? (\d{4})", text):
        month = _MONTHS.get(m[1])
        return date(int(m[2]), month, 1) if month else None
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", text):
        return date(int(m[2]), int(m[1]), 1) if 1 <= int(m[1]) <= 12 else None
    if m := re.fullmatch(r"(\d{4})", text):
        return date(int(m[1]), 1, 1)
    return None


def _role_years(role: dict, today: date) -> float | None:
    start, end = _when(role.get("start", ""), today), _when(role.get("end", ""), today)
    if start is None or end is None or end < start:
        return None
    return (end - start).days / 365.25


def years(facts: Facts, claimed: float, as_title: str | None = None, today: date | None = None) -> Verdict:
    """"`claimed` years of experience", overall or in one title. Supported only when the
    confirmed profile shows at least that many; NEEDS_USER when the dates can't say."""
    today = today or datetime.now(get_settings().tz).date()  # "present" is today where the user is
    if as_title is None:
        held = facts.profile.get("total_years")
    else:
        spans = [_role_years(r, today) for r in facts.profile.get("roles", []) if _key(r["title"]) == _key(as_title)]
        if not spans:
            return Verdict.UNSUPPORTED  # never held the title at all
        held = None if any(s is None for s in spans) else sum(spans)
    if held is None:
        return Verdict.NEEDS_USER
    return Verdict.SUPPORTED if claimed <= held + 1e-9 else Verdict.UNSUPPORTED


def answer(facts: Facts, key: str, stated: str | None = None) -> Verdict:
    """An answer-bank question. Without `stated`: can it be answered from the bank at all?
    With `stated`: is that exactly what the user answered?

    A blank answer, or a question the bank doesn't have, is NEEDS_USER. "Prefer not to
    say" and "Decline to answer" are answers: stating anything else in their place is
    UNSUPPORTED."""
    value = facts.answers.get(key, "")
    if not value:
        return Verdict.NEEDS_USER
    if stated is None:
        return Verdict.SUPPORTED
    return Verdict.SUPPORTED if normalize(stated) == normalize(value) else Verdict.UNSUPPORTED
