"""The admin panel (#3): invite accounts, make a new set-password link,
disable and re-enable. Every route needs require_admin; a non-admin gets 404."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admin, invites
from app.db import get_db
from app.flash import flash
from app.models import User
from app.security import normalize_email, require_admin
from app.templating import templates

router = APIRouter(prefix="/admin")


def _page(request: Request, db: Session, me: User, *, status: int = 200, **extra) -> HTMLResponse:
    context = {"user": me, "accounts": admin.accounts(db), "pending": invites.pending(db), **extra}
    return templates.TemplateResponse(request, "admin.html", context, status_code=status)


def _link(request: Request, token: str) -> str:
    return str(request.url_for("invite_form", token=token))


@router.get("", response_class=HTMLResponse)
def panel(request: Request, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _page(request, db, me)


@router.post("/invites", response_class=HTMLResponse)
def create_invite(
    request: Request,
    email: Annotated[str, Form(max_length=254)],
    me: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = normalize_email(email)
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        return _page(request, db, me, error="That doesn't look like an email address.", status=422)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        return _page(request, db, me, error=f"{email} already has an account. Use its “New link” button.", status=409)
    token = invites.create(db, email, me)
    db.commit()
    # Rendered, not redirected: the link exists in the clear only in this response.
    return _page(request, db, me, new_link={"email": email, "url": _link(request, token)})


def _account(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    return user


@router.post("/accounts/{user_id}/link", response_class=HTMLResponse)
def new_link(request: Request, user_id: int, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    """A new set-password link for an existing account: how a forgotten
    password is reset. The admin never sees or picks the password."""
    account = _account(db, user_id)
    token = invites.create(db, account.email, me)
    db.commit()
    return _page(request, db, me, new_link={"email": account.email, "url": _link(request, token)})


@router.post("/accounts/{user_id}/disable")
def disable(request: Request, user_id: int, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    account = _account(db, user_id)
    if account.id == me.id:
        flash(request, "You can't disable your own account.")
    else:
        admin.set_disabled(db, account, True)
        db.commit()
        flash(request, f"Disabled {account.email}. Their sessions have ended.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/accounts/{user_id}/enable")
def enable(request: Request, user_id: int, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    account = _account(db, user_id)
    admin.set_disabled(db, account, False)
    db.commit()
    flash(request, f"Re-enabled {account.email}.")
    return RedirectResponse("/admin", status_code=303)
