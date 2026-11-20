"""Applications and site notes (#76): what the /apply skill records through the
bridge after it's done with a job, and what it learned about a site.

A record, written after the fact: the server never touches an employer's site.
`submitted` means Sean clicked Submit himself (non-negotiable 8); `stopped` says
why. The answers used are kept so he can check them against his answer bank
(non-negotiable 7).

⚠️ Private data: every function takes the current user and touches only their rows
(non-negotiable 1).
"""

import base64
import binascii
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import Application, SavedJob, SiteNote, TailoredResume, User

OUTCOMES = ("submitted", "stopped")
NOTE_MAX = 4000
SITE_NOTE_MAX = 20_000
SCREENSHOT_MAX = 5 * 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n"
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


class Invalid(ValueError):
    """Why a record can't be saved, worded for the session."""


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def clean_host(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    if not _HOST.match(host) or len(host) > 255:
        raise Invalid("That isn't a host name, like jobs.example.com.")
    return host


def next_job(db: Session, user: User) -> SavedJob | None:
    """The oldest saved job whose tailored resume Sean approved and that has no
    application recorded yet (submitted or stopped)."""
    approved = exists().where(TailoredResume.saved_job_id == SavedJob.id, TailoredResume.approved_at.is_not(None))
    recorded = exists().where(Application.saved_job_id == SavedJob.id)
    return db.scalar(
        select(SavedJob)
        .where(SavedJob.user_id == user.id, approved, ~recorded)
        .order_by(SavedJob.created_at, SavedJob.id)
        .limit(1)
    )


def get(db: Session, user: User, job: SavedJob) -> Application | None:
    return db.scalar(select(Application).where(Application.user_id == user.id, Application.saved_job_id == job.id))


def record(
    db: Session, user: User, job: SavedJob, *, outcome: str, note: str, answers_used: list, screenshot_b64: str | None
) -> Application:
    """Record (or replace) the application for one job (uncommitted). Invalid on a
    bad outcome, a stop with no reason, or a screenshot that isn't a PNG under 5 MB."""
    if outcome not in OUTCOMES:
        raise Invalid("outcome is submitted or stopped.")
    note = (note or "").strip()
    if outcome == "stopped" and not note:
        raise Invalid("Say why it stopped, in note.")
    if len(note) > NOTE_MAX:
        raise Invalid(f"Keep note under {NOTE_MAX} characters.")
    used = []
    for pair in answers_used[:200]:
        if isinstance(pair, dict) and isinstance(pair.get("question"), str) and isinstance(pair.get("answer"), str):
            used.append({"question": pair["question"][:500], "answer": pair["answer"][:2000]})
    screenshot = None
    if screenshot_b64:
        try:
            screenshot = base64.b64decode(screenshot_b64, validate=True)
        except (binascii.Error, ValueError):
            raise Invalid("screenshot isn't base64.") from None
        if not screenshot.startswith(PNG):
            raise Invalid("screenshot must be a PNG.")
        if len(screenshot) > SCREENSHOT_MAX:
            raise Invalid("screenshot must be under 5 MB.")
    row = get(db, user, job) or Application(user_id=user.id, saved_job_id=job.id)
    row.outcome, row.note, row.answers_used, row.screenshot = outcome, note, used, screenshot
    row.recorded_at = datetime.now(UTC)
    db.add(row)
    db.flush()
    return row


def site_note(db: Session, user: User, host: str) -> SiteNote | None:
    return db.scalar(select(SiteNote).where(SiteNote.user_id == user.id, SiteNote.host == host))


def save_site_note(db: Session, user: User, host: str, note: str) -> SiteNote:
    """Save (or replace) what was learned about a site (uncommitted)."""
    host, note = clean_host(host), (note or "").strip()
    if len(note) > SITE_NOTE_MAX:
        raise Invalid(f"Keep the note under {SITE_NOTE_MAX:,} characters.")
    row = site_note(db, user, host) or SiteNote(user_id=user.id, host=host)
    row.note = note
    db.add(row)
    db.flush()
    return row
