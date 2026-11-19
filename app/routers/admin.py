"""The admin panel (#3): invite accounts, make a new set-password link,
disable and re-enable; and the shared employer registry (#43). Every route
needs require_admin; a non-admin gets 404."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admin, employers, invites
from app.config import get_settings
from app.db import get_db
from app.flash import flash
from app.models import Employer, User
from app.security import normalize_email, require_admin
from app.templating import templates

router = APIRouter(prefix="/admin")


def _page(request: Request, db: Session, me: User, *, status: int = 200, **extra) -> HTMLResponse:
    context = {
        "user": me,
        "accounts": admin.accounts(db),
        "pending": invites.pending(db),
        "spend": admin.month_spend(db),
        "user_budget_cents": get_settings().ai_monthly_budget_cents_per_user,
        **extra,
    }
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


# ---- The employer registry (#43) -----------------------------------------------------


def _employers_page(request: Request, db: Session, me: User, *, status: int = 200, **extra) -> HTMLResponse:
    context = {
        "user": me,
        "employers": employers.all_employers(db),
        "systems": employers.SYSTEMS,
        "supported": employers.SUPPORTED,
        **extra,
    }
    return templates.TemplateResponse(request, "admin_employers.html", context, status_code=status)


@router.get("/employers", response_class=HTMLResponse)
def employer_list(request: Request, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _employers_page(request, db, me)


@router.post("/employers", response_class=HTMLResponse)
def add_employer(
    request: Request,
    careers_url: Annotated[str, Form(max_length=500)],
    name: Annotated[str, Form(max_length=200)] = "",
    me: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        employer = employers.add(db, careers_url, name, me)
    except employers.RegistryError as error:
        db.rollback()
        return _employers_page(request, db, me, error=str(error), form={"careers_url": careers_url, "name": name}, status=422)
    db.commit()
    flash(request, f"Added {employer.name} ({employers.SYSTEMS[employer.system]}, “{employer.token}”).")
    return RedirectResponse("/admin/employers", status_code=303)


def _employer(db: Session, employer_id: int) -> Employer:
    employer = db.get(Employer, employer_id)
    if employer is None:
        raise HTTPException(status_code=404)
    return employer


@router.post("/employers/{employer_id}/disable")
def disable_employer(request: Request, employer_id: int, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    employer = _employer(db, employer_id)
    employer.enabled = False
    db.commit()
    flash(request, f"Disabled {employer.name}. The nightly search skips it.")
    return RedirectResponse("/admin/employers", status_code=303)


@router.post("/employers/{employer_id}/enable")
def enable_employer(request: Request, employer_id: int, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    employer = _employer(db, employer_id)
    employer.enabled = True
    db.commit()
    flash(request, f"Re-enabled {employer.name}.")
    return RedirectResponse("/admin/employers", status_code=303)
