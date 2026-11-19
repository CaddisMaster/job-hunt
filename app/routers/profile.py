"""The profile area (plan §3): resumes (#23), the profile built from one (#24), and
the answer bank (#25).

⚠️ Every route here is the current user's own data: app.resumes scopes every query
to them, and someone else's resume id is a 404."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile  # what request.form() holds (FastAPI's is a subclass)

from app import ai, answers, resumes, spend
from app import profile as profiles
from app.db import get_db
from app.flash import flash
from app.models import User
from app.security import require_user
from app.templating import templates

router = APIRouter(prefix="/profile")


def _page(
    request: Request, db: Session, user: User, *, status: int = 200, error: str | None = None, build_error: str | None = None
) -> HTMLResponse:
    found = resumes.for_user(db, user)
    base = found[0] if found else None
    profile = profiles.get(db, user)
    refusal = spend.refusal(db, user.id)
    context = {
        "user": user,
        "resumes": found,
        "error": error,
        "max_mb": resumes.MAX_BYTES // (1024 * 1024),
        "base": base,
        "profile": profile,
        "stale": bool(profile and base and profile.resume_id != base.id),
        "ai_message": ai.MESSAGES[refusal] if refusal else None,
        "build_error": build_error,
        "role_line": profiles.role_line,
    }
    return templates.TemplateResponse(request, "profile.html", context, status_code=status)


@router.get("", response_class=HTMLResponse)
def profile(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _page(request, db, user)


@router.post("/resumes", response_class=HTMLResponse)
async def upload(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()  # already parsed by the CSRF check, so not read twice
    file = form.get("resume")
    if not isinstance(file, UploadFile):
        return _page(request, db, user, status=400, error="Choose a PDF or Word (.docx) file first.")
    data = await file.read(resumes.MAX_BYTES + 1)  # one byte over is enough to know it's too big
    try:
        resume = resumes.store(db, user, file.filename, data)
    except resumes.Rejected as exc:
        return _page(request, db, user, status=400, error=str(exc))
    if resume.text:
        flash(request, f"{resume.filename} is uploaded and is now your base resume.")
    else:
        flash(
            request,
            f"{resume.filename} is uploaded, but it has no text in it (a scanned PDF?), so a profile "
            "can't be built from it. Upload a PDF exported from your editor, or a .docx, to fix that.",
        )
    return RedirectResponse("/profile", status_code=303)


@router.get("/resumes/{resume_id}/file")
def download(resume_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> Response:
    resume = resumes.get(db, user, resume_id)
    if resume is None:
        raise HTTPException(status_code=404)
    # Always a download, never rendered inline; the name is RFC 5987-encoded.
    disposition = f"attachment; filename=\"resume.{resume.kind}\"; filename*=UTF-8''{quote(resume.filename)}"
    return Response(
        resume.content,
        media_type=resumes.MEDIA_TYPES[resume.kind],
        headers={"Content-Disposition": disposition, "Cache-Control": "private, no-store"},
    )


@router.post("/build", response_class=HTMLResponse)
async def build(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Build the profile from the base resume: one Claude call, charged to this user."""
    form = await request.form()
    existing = profiles.get(db, user)
    if existing and form.get("replace") != "yes":
        return _page(request, db, user, status=400, build_error="Tick the box to replace your current profile first.")
    base = profiles.base_resume(db, user)
    if base is None or not base.text:
        return _page(request, db, user, status=400, build_error="Upload a resume with text in it first.")
    try:
        profiles.build(db, user)
    except ai.AIUnavailable as exc:
        return _page(request, db, user, status=503 if exc.reason == "error" else 200, build_error=exc.message)
    flash(request, "Your profile is built. Check every line below against your resume, fix anything wrong, then confirm.")
    return RedirectResponse("/profile/review", status_code=303)


@router.get("/review", response_class=HTMLResponse)
def review(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    profile = profiles.get(db, user)
    if profile is None:
        return RedirectResponse("/profile", status_code=303)
    context = {"user": user, "profile": profile, "seniority": profiles.SENIORITY, "role_line": profiles.role_line, "edited": profiles.EDITED}
    return templates.TemplateResponse(request, "profile_review.html", context)


@router.post("/review")
async def confirm(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    profile = profiles.get(db, user)
    if profile is None:
        return RedirectResponse("/profile", status_code=303)
    form = await request.form()
    fields = ("roles", "skills", "education", "certifications", "total_years", "seniority", "target_titles")
    profiles.confirm(db, profile, {k: v for k in fields if isinstance(v := form.get(k, ""), str)})
    flash(request, "Your profile is confirmed.")
    return RedirectResponse("/profile", status_code=303)


def _answers_page(request: Request, db: Session, user: User, values: dict, errors: dict, status: int = 200) -> HTMLResponse:
    context = {
        "user": user, "values": values, "errors": errors, "questions": answers.QUESTIONS, "groups": answers.GROUPS,
        "added": answers.added_questions(db, user), "prefer_not": answers.PREFER_NOT,
    }
    return templates.TemplateResponse(request, "answers.html", context, status_code=status)


@router.get("/answers", response_class=HTMLResponse)
def answer_bank(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _answers_page(request, db, user, answers.for_user(db, user), {})


@router.post("/answers", response_class=HTMLResponse)
async def save_answers(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        answers.save(db, user, form)
    except answers.Invalid as exc:
        typed = {q.key: (form.get(q.key) or "").strip() for q in answers.QUESTIONS}
        return _answers_page(request, db, user, typed, exc.errors, status=400)
    flash(request, "Your answers are saved.")
    return RedirectResponse("/profile/answers", status_code=303)
