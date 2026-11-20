"""Tailored resumes (#73): Claude Code chooses, Job Hunt checks, renders and stores.

Tailoring happens in a Claude Code session on Sean's Mac (plan §10 decision 11):
it sends, through the bridge, which of his resume's lines to use for one saved job
and in what order. This module is the strict half. Non-negotiable 7 holds in code,
whatever was sent:

- **Roles, titles, employers and dates** come from the confirmed profile, never from
  what was sent. A role is chosen only by its number in the profile.
- **A bullet** must appear verbatim in the resume, **inside its own role's part**
  (from that role's line to the next role's), so a bullet can't move to another job.
- **Summary sentences, the name and contact details** must appear verbatim in the
  resume.
- **Skills, education and certifications** must be confirmed ones.

Anything that fails is dropped and listed in `left_out` with why; it never reaches
the PDF. Comparison is app/grounding.py's `normalize` (case, Unicode, dashes, quotes,
whitespace), so PDF extraction's broken lines don't count against a real line.

The PDF is plain and one column (ReportLab's built-in Helvetica), real text an
applicant tracking system can read. Helvetica covers Windows-1252, so anything
outside it is written without its accents rather than as a missing glyph.

⚠️ Private data: every function takes the current user and touches only their rows
(non-negotiable 1).
"""

import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import profile as profiles
from app.grounding import normalize
from app.models import Profile, Resume, SavedJob, TailoredResume, User

MODEL = "claude-code"  # who chose the lines: the /apply session, not an API call
MAX_BULLETS = 10  # per role
MAX_LINES = 20  # contact or summary
MAX_ITEMS = 60  # skills, education, certifications
LINE_MAX = 1000

_BULLET = re.compile(r"^\s*(?:[•●▪◦‣∙·*–—-]|\d+[.)])\s+")


class NotReady(Exception):
    """Why a resume can't be tailored for this job yet, worded for the user."""


@dataclass
class Checked:
    content: dict
    left_out: list[dict] = field(default_factory=list)

    def drop(self, section: str, text: str, why: str) -> None:
        self.left_out.append({"section": section, "text": text[:300], "why": why})


def _clean(line: object) -> str:
    return _BULLET.sub("", line).strip()[:LINE_MAX] if isinstance(line, str) else ""


def _segments(roles: list[dict], resume: str) -> list[tuple[int, int] | None]:
    """Each role's part of the normalised resume: from its own line to the next
    role's line (or the end). None when the role's line isn't in the resume (one the
    user typed in themselves), so its bullets can't be placed and are left out."""
    starts = []
    for role in roles:
        source = normalize(role.get("source") or "")
        position = resume.find(source) if source and role.get("source") != profiles.EDITED else -1
        starts.append(position if position >= 0 else None)
    known = sorted(p for p in starts if p is not None)
    segments = []
    for start in starts:
        if start is None:
            segments.append(None)
            continue
        later = [p for p in known if p > start]
        segments.append((start, later[0] if later else len(resume)))
    return segments


def check(sent: dict, profile: dict, resume_text: str) -> Checked:
    """What may go on the resume, from what was sent. Never raises on bad input:
    anything unusable is left out with the reason."""
    resume = normalize(resume_text)
    out = Checked({"name": "", "contact": [], "summary": [], "roles": [], "skills": [], "education": [], "certifications": []})

    def verbatim(section: str, lines: object, limit: int) -> list[str]:
        kept, seen = [], set()
        for line in (lines if isinstance(lines, list) else [])[:limit]:
            text = _clean(line)
            key = normalize(text)
            if not key or key in seen:
                continue
            if key in resume:
                kept.append(text)
                seen.add(key)
            else:
                out.drop(section, text, "not in your resume")
        return kept

    name = _clean(sent.get("name"))
    if name and normalize(name) in resume:
        out.content["name"] = name
    elif name:
        out.drop("name", name, "not in your resume")
    out.content["contact"] = verbatim("contact", sent.get("contact"), MAX_LINES)
    out.content["summary"] = verbatim("summary", sent.get("summary"), MAX_LINES)

    roles = profile.get("roles") or []
    segments = _segments(roles, resume)
    chosen: dict[int, list] = {}
    for entry in sent.get("roles") if isinstance(sent.get("roles"), list) else []:
        number = entry.get("role_number") if isinstance(entry, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(roles):
            out.drop("roles", f"role {number!r}", "there's no such role in your confirmed profile")
            continue
        chosen.setdefault(number, []).extend(entry.get("bullets") if isinstance(entry.get("bullets"), list) else [])
    for index, role in enumerate(roles):
        bullets, seen = [], set()
        segment = segments[index]
        for line in chosen.get(index + 1, []):
            text = _clean(line)
            key = normalize(text)
            if not key or key in seen:
                continue
            if len(bullets) >= MAX_BULLETS:
                out.drop("roles", text, f"more than {MAX_BULLETS} bullets for one role")
            elif segment is None:
                out.drop("roles", text, f"your resume's line for “{role.get('title', '')}” couldn't be found, so its bullets can't be checked")  # fmt: skip
            elif key in resume[segment[0] : segment[1]]:
                bullets.append(text)
                seen.add(key)
            elif key in resume:
                out.drop("roles", text, f"in your resume, but not under “{role.get('title', '')}”")
            else:
                out.drop("roles", text, "not in your resume")
        out.content["roles"].append(
            {k: role.get(k, "") for k in ("title", "employer", "start", "end")} | {"bullets": bullets}
        )

    for section in ("skills", "education", "certifications"):
        confirmed = {normalize(item["value"]): item["value"] for item in profile.get(section) or []}
        kept, seen = [], set()
        for value in (sent.get(section) if isinstance(sent.get(section), list) else [])[:MAX_ITEMS]:
            text = _clean(value)
            key = normalize(text)
            if not key or key in seen:
                continue
            if key in confirmed:
                kept.append(confirmed[key])
                seen.add(key)
            else:
                out.drop(section, text, f"not in your confirmed profile's {section}")
        out.content[section] = kept
    return out


# ---- The PDF ---------------------------------------------------------------------------


# Letters outside Windows-1252 that don't decompose into a letter and an accent.
_PLAIN = str.maketrans({"Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Ħ": "H", "ħ": "h", "ı": "i", "Ŧ": "T", "ŧ": "t"})


def _latin(text: str) -> str:
    """Text Helvetica can draw (Windows-1252): accents dropped from anything else,
    and "?" only for what has no plain letter at all."""
    text = text.translate(_PLAIN)
    try:
        text.encode("cp1252")
        return text
    except UnicodeEncodeError:
        pass
    out = []
    for char in text:
        try:
            char.encode("cp1252")
            out.append(char)
        except UnicodeEncodeError:
            plain = "".join(c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c))
            out.append(plain if plain and all(ord(c) < 128 for c in plain) else "?")
    return "".join(out)


def _p(text: str) -> str:
    return escape(_latin(text))


def render(content: dict, *, title: str = "Resume") -> bytes:
    """The PDF for checked content: one column, plain, real text. Reproducible: the
    same content gives the same bytes."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import HRFlowable, ListFlowable, Paragraph, SimpleDocTemplate, Spacer, Table

    ink, muted = HexColor("#1f2328"), HexColor("#57606a")
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=13, textColor=ink)
    name = ParagraphStyle("name", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22)
    contact = ParagraphStyle("contact", parent=base, fontSize=9, leading=12, textColor=muted)
    heading = ParagraphStyle("heading", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=10)
    role = ParagraphStyle("role", parent=base, fontName="Helvetica-Bold")
    dates = ParagraphStyle("dates", parent=base, textColor=muted, alignment=TA_RIGHT)

    width = LETTER[0] - 1.4 * inch
    story = []
    if content.get("name"):
        story.append(Paragraph(_p(content["name"]), name))
    if content.get("contact"):
        story.append(Paragraph(" · ".join(_p(c) for c in content["contact"]), contact))

    def section(label: str) -> None:
        story.append(Paragraph(_p(label), heading))
        story.append(HRFlowable(width="100%", thickness=0.6, color=muted, spaceBefore=2, spaceAfter=4))

    def bullets(lines: list[str]) -> None:
        items = [Paragraph(_p(line), base) for line in lines]
        story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=12, bulletFontSize=8))

    if content.get("summary"):
        section("Summary")
        story.append(Paragraph(" ".join(_p(s) for s in content["summary"]), base))
    if content.get("roles"):
        section("Experience")
        for r in content["roles"]:
            left = _p(r.get("title", "")) + (f", {_p(r['employer'])}" if r.get("employer") else "")
            when = " – ".join(_p(d) for d in (r.get("start"), r.get("end")) if d)
            row = Table([[Paragraph(left, role), Paragraph(when, dates)]], colWidths=[width * 0.72, width * 0.28])
            row.setStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP")])  # fmt: skip
            story.append(Spacer(1, 4))
            story.append(row)
            if r.get("bullets"):
                bullets(r["bullets"])
    if content.get("skills"):
        section("Skills")
        story.append(Paragraph(", ".join(_p(s) for s in content["skills"]), base))
    for key, label in (("education", "Education"), ("certifications", "Certifications")):
        if content.get(key):
            section(label)
            bullets(content[key])

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER, leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.6 * inch,
        bottomMargin=0.6 * inch, title=_latin(title), author=_latin(content.get("name") or ""), invariant=1,
    )  # fmt: skip
    doc.build(story)
    return buffer.getvalue()


# ---- Storing -----------------------------------------------------------------------------


def get(db: Session, user: User, job: SavedJob) -> TailoredResume | None:
    return db.scalar(select(TailoredResume).where(TailoredResume.user_id == user.id, TailoredResume.saved_job_id == job.id))


def store(db: Session, user: User, job: SavedJob, sent: dict) -> TailoredResume:
    """Check what was sent, make the PDF and store it for the job (uncommitted),
    replacing any earlier one and clearing its approval. NotReady when the job has
    no posting text, or there's no confirmed profile or resume text to check against."""
    if job.user_id != user.id:
        raise NotReady("That isn't your job.")  # callers already 404; belt and braces
    if job.source == "pending":
        raise NotReady("That job is still waiting for its posting text.")
    profile: Profile | None = profiles.get(db, user)
    if profile is None or profile.reviewed_at is None:
        raise NotReady("Confirm your profile first: a tailored resume is checked against it.")
    resume: Resume | None = profiles.base_resume(db, user)
    if resume is None or not resume.text:
        raise NotReady("Upload a resume with text first: a tailored resume is checked against it.")
    checked = check(sent, profile.data, resume.text)
    title = f"{checked.content['name'] or 'Resume'}, {job.title}" if job.title else "Resume"
    row = get(db, user, job) or TailoredResume(user_id=user.id, saved_job_id=job.id)
    row.resume_id = resume.id
    row.content, row.left_out = checked.content, checked.left_out
    row.pdf = render(checked.content, title=title)
    row.model, row.created_at, row.approved_at = MODEL, datetime.now(UTC), None
    db.add(row)
    db.flush()
    return row


def approve(row: TailoredResume) -> None:
    row.approved_at = row.approved_at or datetime.now(UTC)


def filename(row: TailoredResume, job: SavedJob) -> str:
    """"Jordan Example - Resume.pdf": what the employer sees when it's uploaded."""
    name = re.sub(r"[^\w .-]", "", _latin(row.content.get("name") or "")).strip() or "Resume"
    return f"{name} - Resume.pdf" if name != "Resume" else "Resume.pdf"


def pdf_response(row: TailoredResume, job: SavedJob) -> Response:
    """The PDF as a download: never cached, and `X-Approved` says whether it's the
    one approved for applying."""
    return Response(
        row.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename(row, job)}"',
            "X-Approved": "true" if row.approved_at else "false",
            "Cache-Control": "private, no-store",
        },
    )
