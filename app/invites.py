"""Invites: one-time, expiring set-password links (#3).

Job Hunt is invite only (plan §2). The admin makes a link and sends it
themselves; there's no mailer until 0.4. Opening the link and choosing a
password creates the account, or resets the password of one that exists, so
the admin never chooses or sees anyone's password.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Invite, User, new_session_token
from app.security import hash_password

INVITE_LIFETIME = timedelta(days=7)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(db: Session, email: str, created_by: User, *, now: datetime | None = None) -> str:
    """Make a link for `email` and return its token, the only time it exists in
    the clear. Any older unused link for the same email stops working."""
    now = now or datetime.now(UTC)
    db.execute(update(Invite).where(Invite.email == email, Invite.used_at.is_(None)).values(used_at=now))
    token = secrets.token_urlsafe(32)
    db.add(
        Invite(
            email=email, token_hash=_hash(token), created_by=created_by.id, created_at=now, expires_at=now + INVITE_LIFETIME
        )
    )
    return token


@dataclass(frozen=True)
class Lookup:
    invite: Invite | None
    status: str  # "ok", "expired" or "invalid" (unknown or already used)


def find(db: Session, token: str, *, now: datetime | None = None) -> Lookup:
    now = now or datetime.now(UTC)
    invite = db.scalar(select(Invite).where(Invite.token_hash == _hash(token)))
    if invite is None or invite.used_at is not None:
        return Lookup(None, "invalid")
    if invite.expires_at <= now:
        return Lookup(invite, "expired")
    return Lookup(invite, "ok")


def accept(db: Session, invite: Invite, password: str, *, now: datetime | None = None) -> User:
    """Spend the invite: create the account, or set a new password on the one
    that exists (ending its sessions). A disabled account stays disabled."""
    now = now or datetime.now(UTC)
    invite.used_at = now
    user = db.scalar(select(User).where(User.email == invite.email))
    if user is None:
        user = User(email=invite.email, password_hash=hash_password(password))
        db.add(user)
    else:
        user.password_hash = hash_password(password)
        user.session_token = new_session_token()
    db.flush()
    return user


def pending(db: Session, *, now: datetime | None = None) -> list[Invite]:
    now = now or datetime.now(UTC)
    return list(
        db.scalars(
            select(Invite).where(Invite.used_at.is_(None), Invite.expires_at > now).order_by(Invite.created_at.desc())
        )
    )
