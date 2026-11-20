"""Personal access tokens for the bridge (#74): how Claude Code on Sean's Mac calls
the JSON API under /api/.

A token is `jh_` plus 32 random bytes (URL-safe base64). It's shown to its owner
once; only its SHA-256 is stored, so a database leak can't be replayed. A token
works for its owner only (non-negotiable 1), until it's revoked or the account is
disabled. The API takes no session cookie, so a page can't make the browser call
it on someone's behalf (that's why /api/ needs no CSRF token).

⚠️ Every function takes the current user and touches only their rows, except
`authenticate`, which finds the token's owner.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiToken, User

PREFIX = "jh_"
NAME_MAX = 100


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create(db: Session, user: User, name: str) -> tuple[str, ApiToken]:
    """A new token (uncommitted): (the token itself, to show once, and its row)."""
    name = " ".join((name or "").split())[:NAME_MAX] or "Claude Code"
    raw = PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(user_id=user.id, name=name, prefix=raw[:10], token_hash=_hash(raw))
    db.add(row)
    db.flush()
    return raw, row


def for_user(db: Session, user: User) -> list[ApiToken]:
    return list(db.scalars(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc(), ApiToken.id.desc())))  # fmt: skip


def revoke(db: Session, user: User, token_id: int) -> ApiToken:
    """Revoke the user's own token (uncommitted); someone else's is a 404."""
    row = db.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id))
    if row is None:
        raise HTTPException(status_code=404)
    row.revoked_at = row.revoked_at or datetime.now(UTC)
    return row


def authenticate(db: Session, raw: str | None) -> User | None:
    """The owner of a live token, or None. Records when it was last used."""
    if not raw or not raw.startswith(PREFIX) or len(raw) > 200:
        return None
    digest = _hash(raw)
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
    if row is None or row.revoked_at is not None or not hmac.compare_digest(row.token_hash, digest):
        return None
    user = db.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        return None
    row.last_used_at = datetime.now(UTC)
    db.commit()
    return user
