"""Opening an invite link and choosing a password (#3). No login needed: the
link is the credential, and it works once."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import invites
from app.db import get_db
from app.security import PASSWORD_MIN_LENGTH, login_session
from app.templating import templates

router = APIRouter()

MESSAGES = {
    "expired": "This link has expired. Ask the admin for a new one.",
    "invalid": "This link isn't valid. It may have been used already. Ask the admin for a new one.",
}


def _refused(request: Request, status: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "invite.html", {"refused": MESSAGES[status]}, status_code=410 if status == "expired" else 404
    )


@router.get("/invite/{token}", response_class=HTMLResponse, name="invite_form")
def invite_form(request: Request, token: str, db: Session = Depends(get_db)):
    found = invites.find(db, token)
    if found.status != "ok":
        return _refused(request, found.status)
    return templates.TemplateResponse(
        request, "invite.html", {"email": found.invite.email, "min_length": PASSWORD_MIN_LENGTH}
    )


@router.post("/invite/{token}", response_class=HTMLResponse)
def accept_invite(
    request: Request,
    token: str,
    password: Annotated[str, Form(max_length=1024)],
    repeat: Annotated[str, Form(max_length=1024)],
    db: Session = Depends(get_db),
):
    found = invites.find(db, token)
    if found.status != "ok":
        return _refused(request, found.status)
    error = None
    if len(password) < PASSWORD_MIN_LENGTH:
        error = f"Use at least {PASSWORD_MIN_LENGTH} characters."
    elif password != repeat:
        error = "The two passwords don't match."
    if error:
        context = {"email": found.invite.email, "min_length": PASSWORD_MIN_LENGTH, "error": error}
        return templates.TemplateResponse(request, "invite.html", context, status_code=422)
    user = invites.accept(db, found.invite, password)
    db.commit()
    if user.disabled_at is not None:
        return RedirectResponse("/login", status_code=303)
    login_session(request, user)
    return RedirectResponse("/", status_code=303)
