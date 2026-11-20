"""Saved jobs (#63): paste a link to a job you found, and Job Hunt reads it.

⚠️ Every route here is the current user's own data: app.saved scopes every query to
them, and someone else's job id is a 404."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import saved, tailor
from app.db import get_db
from app.flash import flash
from app.models import SavedJob, User
from app.security import require_user
from app.templating import templates

router = APIRouter(prefix="/saved")


def _list(request: Request, db: Session, user: User, *, status: int = 200, **extra) -> HTMLResponse:
    context = {"user": user, "jobs": saved.for_user(db, user), **extra}
    return templates.TemplateResponse(request, "saved.html", context, status_code=status)


def _job(request: Request, user: User, job: SavedJob, *, status: int = 200, db: Session | None = None, **extra) -> HTMLResponse:
    tailored = tailor.get(db, user, job) if db is not None else None
    context = {"user": user, "job": job, "tailored": tailored, **extra}
    return templates.TemplateResponse(request, "saved_job.html", context, status_code=status)


@router.get("", response_class=HTMLResponse)
def saved_jobs(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _list(request, db, user)


@router.post("", response_class=HTMLResponse)
def paste(
    request: Request,
    url: Annotated[str, Form(max_length=2000)],
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        job, new = saved.paste(db, user, url)
    except saved.LinkError as error:
        db.rollback()
        return _list(request, db, user, error=str(error), form={"url": url}, status=422)
    db.commit()
    if not new and job.source != "pending":
        flash(request, "You'd already saved that job.")
    elif job.source != "pending":
        flash(request, f"Saved {job.title}.")
    return RedirectResponse(f"/saved/{job.id}", status_code=303)


@router.get("/{job_id}", response_class=HTMLResponse)
def view(request: Request, job_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _job(request, user, saved.get(db, user, job_id), db=db)


@router.post("/{job_id}/text", response_class=HTMLResponse)
def add_text(
    request: Request,
    job_id: int,
    title: Annotated[str, Form(max_length=300)],
    text: Annotated[str, Form(max_length=60_000)],
    company: Annotated[str, Form(max_length=200)] = "",
    location: Annotated[str, Form(max_length=500)] = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    job = saved.get(db, user, job_id)
    try:
        saved.add_text(db, job, title=title, company=company, location=location, text=text)
    except saved.LinkError as error:
        db.rollback()
        form = {"title": title, "company": company, "location": location, "text": text}
        return _job(request, user, saved.get(db, user, job_id), db=db, error=str(error), form=form, status=422)
    db.commit()
    flash(request, f"Saved {job.title}.")
    return RedirectResponse(f"/saved/{job.id}", status_code=303)


@router.get("/{job_id}/resume.pdf")
def tailored_pdf(job_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = saved.get(db, user, job_id)
    row = tailor.get(db, user, job)
    if row is None:
        raise HTTPException(status_code=404)
    return tailor.pdf_response(row, job)


@router.post("/{job_id}/resume/approve")
def approve_tailored(request: Request, job_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = saved.get(db, user, job_id)
    row = tailor.get(db, user, job)
    if row is None:
        raise HTTPException(status_code=404)
    tailor.approve(row)
    db.commit()
    flash(request, "Approved: this is the resume used to apply.")
    return RedirectResponse(f"/saved/{job.id}", status_code=303)


@router.post("/{job_id}/delete")
def delete(request: Request, job_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = saved.get(db, user, job_id)
    title = job.title or job.url
    db.delete(job)
    db.commit()
    flash(request, f"Removed {title}.")
    return RedirectResponse("/saved", status_code=303)
