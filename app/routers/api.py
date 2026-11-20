"""The bridge's JSON API (#74): what Claude Code on Sean's Mac calls, with a
personal access token (app/tokens.py). Never the session cookie, so no CSRF.

Errors are JSON, never a page or an exception (non-negotiable 3): main.py's
handlers answer /api/ paths with {"error": …}.

⚠️ Every route is the token owner's own data (non-negotiable 1): someone else's
saved job is a 404, the same as one that doesn't exist.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import answers, saved, tailor
from app import profile as profiles
from app.db import get_db
from app.models import SavedJob, User
from app.security import require_token

router = APIRouter(prefix="/api")


def _tailored(db: Session, user: User, job: SavedJob) -> dict | None:
    row = tailor.get(db, user, job)
    if row is None:
        return None
    return {
        "approved": row.approved_at is not None,
        "made_at": row.created_at.isoformat(),
        "content": row.content,
        "left_out": row.left_out,
        "pdf": f"/api/jobs/{job.id}/resume.pdf",
        "pdf_filename": tailor.filename(row, job),
    }


def _job(job: SavedJob, tailored: dict | None = None) -> dict:
    return {
        "id": job.id,
        "url": job.url,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "remote": job.remote,
        "read_from": job.source,
        "has_posting": job.source != "pending",
        "saved_at": job.created_at.isoformat(),
        "tailored_resume": "approved" if tailored and tailored["approved"] else "draft" if tailored else None,
        # Only a job whose tailored resume Sean approved is applied to (#73).
        "ready_to_apply": bool(tailored and tailored["approved"]),
    }


@router.get("/jobs")
def jobs(user: User = Depends(require_token), db: Session = Depends(get_db)):
    """My saved jobs, newest first."""
    return {"jobs": [_job(job, _tailored(db, user, job)) for job in saved.for_user(db, user)]}


@router.get("/jobs/{job_id}/package")
def package(job_id: int, user: User = Depends(require_token), db: Session = Depends(get_db)):
    """Everything Claude Code needs to tailor a resume and fill a form for one job:
    the posting, the base resume's text, the confirmed profile and the answer bank.
    The profile is null until it's confirmed; nothing may be claimed before then."""
    job = saved.get(db, user, job_id)
    resume = profiles.base_resume(db, user)
    profile = profiles.get(db, user)
    confirmed = profile is not None and profile.reviewed_at is not None
    bank = answers.for_user(db, user)
    tailored = _tailored(db, user, job)
    return {
        "job": {**_job(job, tailored), "posting": job.description},
        "tailored_resume": tailored,
        "resume": {"filename": resume.filename, "text": resume.text} if resume else None,
        "profile": profile.data if confirmed else None,
        "answers": {
            "starter": [{"key": q.key, "question": q.label, "answer": bank[q.key]} for q in answers.QUESTIONS],
            "added": [{"key": a.key, "question": a.question, "answer": a.answer} for a in answers.added_questions(db, user)],
        },
    }


class NewAnswer(BaseModel):
    answer: str = Field(max_length=answers.MAX_ANSWER + 100)
    key: str | None = Field(default=None, max_length=64)
    question: str | None = Field(default=None, max_length=answers.QUESTION_MAX + 100)


@router.post("/answers", status_code=201)
def add_answer(body: NewAnswer, user: User = Depends(require_token), db: Session = Depends(get_db)):
    """Add an answer Sean gave during an application to his answer bank: a starter
    question by `key`, or a new one by its wording."""
    try:
        row = answers.add(db, user, answer=body.answer, key=body.key, question=body.question)
    except answers.Invalid as invalid:
        db.rollback()
        return JSONResponse({"error": "invalid", "fields": invalid.errors}, status_code=422)
    db.commit()
    return {"key": row.key, "question": row.question or answers.BY_KEY[row.key].label, "answer": row.answer}



# ---- The tailored resume (#73) ------------------------------------------------------------


class SentRole(BaseModel):
    role_number: int
    bullets: list[str] = Field(default_factory=list, max_length=50)


class SentResume(BaseModel):
    """Which of the resume's lines to use, chosen in Claude Code. Every line is checked
    against the resume and the confirmed profile before it's kept (app/tailor.py)."""

    name: str = Field(default="", max_length=200)
    contact: list[str] = Field(default_factory=list, max_length=50)
    summary: list[str] = Field(default_factory=list, max_length=50)
    roles: list[SentRole] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=200)
    education: list[str] = Field(default_factory=list, max_length=100)
    certifications: list[str] = Field(default_factory=list, max_length=100)


@router.post("/jobs/{job_id}/tailored", status_code=201)
def send_tailored(job_id: int, body: SentResume, user: User = Depends(require_token), db: Session = Depends(get_db)):
    """Check, render and store a tailored resume for one saved job. The answer says
    what was kept and what was left out (and why), so the session can tell Sean.
    A new one replaces the old and needs approving again."""
    job = saved.get(db, user, job_id)
    try:
        tailor.store(db, user, job, body.model_dump())
    except tailor.NotReady as reason:
        db.rollback()
        return JSONResponse({"error": str(reason)}, status_code=409)
    db.commit()
    return _tailored(db, user, job)


@router.post("/jobs/{job_id}/tailored/approve")
def approve_tailored(job_id: int, user: User = Depends(require_token), db: Session = Depends(get_db)):
    """Sean approved it in the session: it's now the one used to apply."""
    job = saved.get(db, user, job_id)
    row = tailor.get(db, user, job)
    if row is None:
        raise HTTPException(status_code=404)
    tailor.approve(row)
    db.commit()
    return _tailored(db, user, job)


@router.get("/jobs/{job_id}/resume.pdf")
def tailored_pdf(job_id: int, user: User = Depends(require_token), db: Session = Depends(get_db)):
    """The tailored PDF, approved or not (the session shows a draft to Sean too);
    `X-Approved` says which."""
    job = saved.get(db, user, job_id)
    row = tailor.get(db, user, job)
    if row is None:
        raise HTTPException(status_code=404)
    return tailor.pdf_response(row, job)

