"""The profile (#24): read from the base resume in a Claude Code session, then
checked and confirmed by its owner (plan §3, §10 decision 11).

Non-negotiable 7 starts here. Claude Code is told to extract, never infer, and to
copy the resume line behind every role, skill, degree and certification; it sends
that through the bridge (`POST /api/profile`). Then `ground()` keeps only items
whose line really is in the resume and whose value really is in that line
(app/grounding.py's `in_source`). The session's word is never taken for it. What
survives is shown to the owner beside its line; only a profile they confirm is used.

⚠️ Every function takes the current user and touches only their rows
(non-negotiable 1).
"""

import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import grounding, resumes
from app.models import Profile, Resume, User

EDITED = "added or changed by you"  # the source of anything the owner typed in themselves
normalize = grounding.normalize


# What a session sends: the shape of the extraction (#24's, unchanged).
class SourcedItem(BaseModel):
    value: str = Field(max_length=300, description="The skill, degree or certification, as the resume words it.")
    source: str = Field(max_length=1000, description="The exact line or phrase from the resume it comes from.")


class Role(BaseModel):
    title: str = Field(max_length=300)
    employer: str = Field(default="", max_length=300)
    start: str = Field(default="", max_length=50)
    end: str = Field(default="", max_length=50)
    source: str = Field(max_length=1000, description="The exact resume line holding this title.")


class ProfileData(BaseModel):
    roles: list[Role] = Field(default_factory=list, max_length=100)
    skills: list[SourcedItem] = Field(default_factory=list, max_length=300)
    education: list[SourcedItem] = Field(default_factory=list, max_length=50)
    certifications: list[SourcedItem] = Field(default_factory=list, max_length=100)
    total_years: float | None = Field(default=None, ge=0, le=70)
    seniority: str = "unknown"
    target_titles: list[str] = Field(default_factory=list, max_length=50)


def _grounded(value: str, source: str, resume: str) -> bool:
    return grounding.in_source(value, source, resume)


def ground(data: ProfileData, resume_text: str) -> tuple[dict, list[dict]]:
    """The extraction as stored, and what was dropped: only items the resume itself
    supports.

    A role needs its title in its source line and that line in the resume. A skill,
    degree or certification needs its value in its line, and the line in the resume.
    Target titles are search terms, not claims, so they're kept as given."""
    resume = normalize(resume_text)
    left_out: list[dict] = []
    roles = []
    for r in data.roles:
        if _grounded(r.title, r.source, resume):
            roles.append(r.model_dump())
        else:
            left_out.append({"section": "roles", "text": r.title[:300], "why": "its line isn't in your resume, or doesn't name that title"})

    def items(section: str, xs: list[SourcedItem]) -> list[dict]:
        seen, out = set(), []
        for x in xs:
            key = normalize(x.value)
            if key in seen:
                continue
            if _grounded(x.value, x.source, resume):
                seen.add(key)
                out.append({"value": x.value.strip(), "source": x.source.strip()})
            else:
                left_out.append({"section": section, "text": x.value[:300], "why": "its line isn't in your resume, or doesn't say it"})
        return out

    data_out = {
        "roles": roles,
        "skills": items("skills", data.skills),
        "education": items("education", data.education),
        "certifications": items("certifications", data.certifications),
        "total_years": data.total_years,
        "seniority": data.seniority if data.seniority in SENIORITY else "unknown",
        "target_titles": _dedupe(data.target_titles),
    }
    return data_out, left_out


def _dedupe(values: list[str]) -> list[str]:
    seen, out = set(), []
    for v in (v.strip() for v in values):
        if v and normalize(v) not in seen:
            seen.add(normalize(v))
            out.append(v)
    return out


def base_resume(db: Session, user: User) -> Resume | None:
    found = resumes.for_user(db, user)
    return found[0] if found else None


def get(db: Session, user: User) -> Profile | None:
    return db.scalar(select(Profile).where(Profile.user_id == user.id))


class NoResume(Exception):
    """There's no base resume with text to check an extraction against."""


def store(db: Session, user: User, data: ProfileData, *, model: str = "claude-code") -> tuple[Profile, list[dict]]:
    """Ground what a session read from the base resume and store it as the user's
    profile, unconfirmed until they review it (committed): (the profile, what was
    left out). NoResume if there's nothing to check it against."""
    resume = base_resume(db, user)
    if resume is None or not resume.text:
        raise NoResume
    grounded, left_out = ground(data, resume.text)
    profile = get(db, user) or Profile(user_id=user.id)
    profile.resume_id = resume.id
    profile.data = grounded
    profile.model = model[:64]
    profile.built_at = datetime.now(UTC)
    profile.reviewed_at = None
    db.add(profile)
    db.commit()
    return profile, left_out


# --- the owner's review -------------------------------------------------------------
SENIORITY = ["entry", "mid", "senior", "lead", "executive", "unknown"]
_ROLE_LINE = re.compile(r"^(?P<title>.+?)(?:\s+at\s+(?P<employer>.+?))?(?:\s*\((?P<dates>[^)]*)\))?$")


def lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _kept(old: list[dict], values: list[str]) -> list[dict]:
    """Items as the owner left them: an unchanged value keeps its resume line, and
    anything new or reworded is theirs."""
    by_value = {normalize(item["value"]): item for item in old}
    out = []
    for value in _dedupe(values):
        out.append(by_value.get(normalize(value)) or {"value": value, "source": EDITED})
    return out


def role_line(role: dict) -> str:
    dates = " – ".join(d for d in (role.get("start"), role.get("end")) if d)
    line = role["title"]
    if role.get("employer"):
        line += f" at {role['employer']}"
    if dates:
        line += f" ({dates})"
    return line


def _parse_role(line: str) -> dict:
    """"Title at Employer (start – end)", the shape role_line() writes, parts optional."""
    m = _ROLE_LINE.match(line)
    start, _, end = (m["dates"] or "").partition("–")
    return {
        "title": m["title"].strip(), "employer": (m["employer"] or "").strip(),
        "start": start.strip(), "end": end.strip(), "source": EDITED,
    }


def _roles(old: list[dict], text: str) -> list[dict]:
    """Roles as the owner left them: an unchanged line keeps its resume line."""
    by_line = {normalize(role_line(r)): r for r in old}
    return [by_line.get(normalize(line)) or _parse_role(line) for line in lines(text)]


def confirm(db: Session, profile: Profile, form: dict[str, str]) -> Profile:
    """Save the owner's edits and mark the profile reviewed. Items they left alone keep
    their resume lines; anything they added or reworded is recorded as theirs."""
    old = profile.data
    seniority = form.get("seniority", "unknown")
    try:
        years = float(form["total_years"]) if form.get("total_years", "").strip() else None
    except ValueError:
        years = old.get("total_years")
    profile.data = {
        "roles": _roles(old.get("roles", []), form.get("roles", "")),
        "skills": _kept(old.get("skills", []), lines(form.get("skills", ""))),
        "education": _kept(old.get("education", []), lines(form.get("education", ""))),
        "certifications": _kept(old.get("certifications", []), lines(form.get("certifications", ""))),
        "total_years": years if years is None or 0 <= years <= 70 else old.get("total_years"),
        "seniority": seniority if seniority in SENIORITY else "unknown",
        "target_titles": _dedupe(lines(form.get("target_titles", ""))),
    }
    profile.reviewed_at = datetime.now(UTC)
    db.commit()
    return profile
