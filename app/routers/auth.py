"""Login and logout. Accounts are invite-only: created by the admin (#3) or by
scripts/create_user.py, never through a public sign-up."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import client_ip, current_user, login_limiter, login_session, normalize_email, verify_password
from app.templating import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=1024)],
    db: Session = Depends(get_db),
):
    if not login_limiter.hit(client_ip(request)):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Too many attempts. Wait a minute and try again."}, status_code=429
        )
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if not verify_password(user.password_hash if user else None, password) or user.disabled_at is not None:
        # One message for every case: never reveal whether the account exists
        # or is disabled.
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong email or password."}, status_code=401
        )
    user.last_login_at = datetime.now(UTC)
    db.commit()
    login_session(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
