"""The profile (#24): built from the base resume by one Claude call, then checked
and confirmed by its owner (plan §3).

Non-negotiable 7 starts here. The model is told to extract, never infer, and to
copy the resume line behind every role, skill, degree and certification. Then
`ground()` keeps only items whose line really is in the resume and whose value
really is in that line. The model's word is never taken for it. What survives is
shown to the owner beside its line; only a profile they confirm is used later.

⚠️ Every function takes the current user and touches only their rows
(non-negotiable 1).
"""

import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai, resumes, spend
from app.models import Profile, Resume, User

EDITED = "added or changed by you"  # the source of anything the owner typed in themselves
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def normalize(text: str) -> str:
    """For comparing a quoted line with the resume: case, Unicode forms, dashes, quotes and
    runs of whitespace (PDF extraction breaks lines and spaces unpredictably) don't count."""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _grounded(value: str, source: str, resume: str) -> bool:
    value, source = normalize(value), normalize(source)
    return bool(value) and bool(source) and source in resume and value in source


def ground(data: ai.ProfileData, resume_text: str) -> dict:
    """The extraction as stored: only items the resume itself supports.

    A role needs its title in its source line and that line in the resume. A skill,
    degree or certification needs its value in its line, and the line in the resume.
    Target titles are search terms, not claims, so they're kept as given."""
    resume = normalize(resume_text)
    roles = [r.model_dump() for r in data.roles if _grounded(r.title, r.source, resume)]

    def items(xs: list[ai.SourcedItem]) -> list[dict]:
        seen, out = set(), []
        for x in xs:
            key = normalize(x.value)
            if key not in seen and _grounded(x.value, x.source, resume):
                seen.add(key)
                out.append({"value": x.value.strip(), "source": x.source.strip()})
        return out

    return {
        "roles": roles,
        "skills": items(data.skills),
        "education": items(data.education),
        "certifications": items(data.certifications),
        "total_years": data.total_years,
        "seniority": data.seniority,
        "target_titles": _dedupe(data.target_titles),
    }


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


def build(db: Session, user: User) -> Profile:
    """Build (or rebuild) the user's profile from their base resume. One Claude call,
    charged to them and refused over budget (AIUnavailable). The new profile is
    unreviewed until they confirm it."""
    resume = base_resume(db, user)
    if resume is None or not resume.text:
        raise ValueError("no resume with text")  # the page only offers the button when there is one
    response = spend.run(
        user.id, "profile", lambda client, model: ai._call_profile_model(client, model, resume_text=resume.text)
    )
    if getattr(response, "stop_reason", None) == "refusal" or response.parsed_output is None:
        raise ai.AIUnavailable("error")  # charged (spend.run recorded it), but nothing usable came back
    data = ground(response.parsed_output, resume.text)
    profile = get(db, user) or Profile(user_id=user.id)
    profile.resume_id = resume.id
    profile.data = data
    profile.model = getattr(response, "model", None) or ai.MODELS["profile"]
    profile.built_at = datetime.now(UTC)
    profile.reviewed_at = None
    db.add(profile)
    db.commit()
    return profile


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
