"""Passwords, sessions, CSRF and security headers. Rate limiting is in
app/rate_limit.py, because it lives in Postgres.

Ported from Orbit Ops's app/security.py. The difference that matters: every
limiter here is shared and survives restarts (CLAUDE.md, plan §2).
"""

import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app import tokens
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.rate_limit import RateLimiter

# ---------------------------------------------------------------------------
# Passwords — argon2id.
# ---------------------------------------------------------------------------
_hasher = PasswordHasher()
# Verified against when the email does not exist, so a miss costs the same time
# as a wrong password and response timing cannot enumerate accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

PASSWORD_MIN_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ---------------------------------------------------------------------------
# Sessions. The signed cookie carries the user id and that user's
# session_token; both must match the row on every request.
# ---------------------------------------------------------------------------
class LoginRequired(Exception):
    """Raised by require_user; app.main turns it into a redirect to /login."""


def login_session(request: Request, user: User) -> None:
    request.session.clear()  # fresh session on login: no fixation
    request.session["uid"] = user.id
    request.session["tok"] = user.session_token
    request.session["csrf"] = secrets.token_urlsafe(32)


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid, tok = request.session.get("uid"), request.session.get("tok")
    if uid is None or tok is None:
        return None
    user = db.scalar(select(User).where(User.id == uid))
    if user is None or user.disabled_at is not None or not hmac.compare_digest(user.session_token, tok):
        request.session.clear()
        return None
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise LoginRequired
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    """A logged-in non-admin gets the same 404 as a page that doesn't exist, so
    the admin panel isn't advertised."""
    if not user.is_admin:
        raise HTTPException(status_code=404)
    return user


# ---------------------------------------------------------------------------
# CSRF — a per-session token, sent back as the X-CSRF-Token header (htmx, via
# hx-headers on <body>) or as a `csrf_token` form field. Applied app-wide.
# ---------------------------------------------------------------------------
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CSRFError(Exception):
    pass


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if token is None:
        token = request.session["csrf"] = secrets.token_urlsafe(32)
    return token


async def csrf_protect(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    if request.url.path.startswith("/api/"):
        # The API authenticates by bearer token only, never the session cookie
        # (require_token), so a page can't make a browser call it: no CSRF to stop.
        return
    expected = request.session.get("csrf")
    sent = request.headers.get("x-csrf-token")
    if sent is None and request.headers.get("content-type", "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        # Starlette caches the parsed form, so the route's Form() parameters still see it.
        sent = (await request.form()).get("csrf_token")
    if not expected or not isinstance(sent, str) or not hmac.compare_digest(expected, sent):
        raise CSRFError


# ---------------------------------------------------------------------------
# Rate limiting (Postgres-backed, app/rate_limit.py).
# ---------------------------------------------------------------------------
login_limiter = RateLimiter("login", get_settings().login_rate_limit)
# Wrong API tokens per client address per minute (#74). Only failures count.
api_auth_limiter = RateLimiter("api-auth", 20)


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers has already replaced this with Nginx's
    # X-Forwarded-For, and the port is bound to 127.0.0.1 so only Nginx can set it.
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# HEAD → GET. FastAPI's APIRoute doesn't answer HEAD for a GET route, and uptime
# monitors use HEAD (Orbit Ops #9). Serve it as the GET and drop the body.
# ---------------------------------------------------------------------------
class HeadAsGetMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message):
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


# ---------------------------------------------------------------------------
# Security headers, with a fresh CSP nonce per request for inline scripts.
# Never add 'unsafe-inline' or 'unsafe-eval'.
# ---------------------------------------------------------------------------
def build_csp(nonce: str) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = build_csp(nonce)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if get_settings().cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# ---------------------------------------------------------------------------
# The bridge's API (#74): a bearer token, never the session cookie.
# ---------------------------------------------------------------------------
class TokenRequired(Exception):
    """401 for /api/: no token, or not a live one."""


class TooManyAttempts(Exception):
    """429 for /api/: too many wrong tokens from this address."""


def require_token(request: Request, db: Session = Depends(get_db)) -> User:
    """The owner of the request's bearer token. Wrong tokens are counted per address
    in Postgres (non-negotiable 2); past the limit, 429."""
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    user = tokens.authenticate(db, raw.strip()) if scheme.lower() == "bearer" else None
    if user is None:
        if not api_auth_limiter.hit(client_ip(request)):
            raise TooManyAttempts
        raise TokenRequired
    return user
