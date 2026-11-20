"""The bridge's JSON API (#74): what Claude Code on Sean's Mac calls, with a
personal access token (app/tokens.py). Never the session cookie, so no CSRF.

Errors are JSON, never a page or an exception (non-negotiable 3): main.py's
handlers answer /api/ paths with {"error": …}.

⚠️ Every route is the token owner's own data (non-negotiable 1): someone else's
saved job is a 404, the same as one that doesn't exist.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import answers, saved
from app import profile as profiles
from app.db import get_db
from app.models import SavedJob, User
from app.security import require_token

router = APIRouter(prefix="/api")


def _job(job: SavedJob) -> dict:
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
    }


@router.get("/jobs")
def jobs(user: User = Depends(require_token), db: Session = Depends(get_db)):
    """My saved jobs, newest first."""
    return {"jobs": [_job(job) for job in saved.for_user(db, user)]}


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
    return {
        "job": {**_job(job), "posting": job.description},
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

