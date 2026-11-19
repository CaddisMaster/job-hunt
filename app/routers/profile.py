"""The profile area (plan §3). For now, resumes (#23); the profile built from one
(#24) and the answer bank (#25) join this page.

⚠️ Every route here is the current user's own data: app.resumes scopes every query
to them, and someone else's resume id is a 404."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile  # what request.form() holds (FastAPI's is a subclass)

from app import resumes
from app.db import get_db
from app.flash import flash
from app.models import User
from app.security import require_user
from app.templating import templates

router = APIRouter(prefix="/profile")


def _page(request: Request, db: Session, user: User, *, status: int = 200, error: str | None = None) -> HTMLResponse:
    context = {"user": user, "resumes": resumes.for_user(db, user), "error": error, "max_mb": resumes.MAX_BYTES // (1024 * 1024)}
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
