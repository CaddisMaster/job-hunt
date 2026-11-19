"""ORM models.

⚠️ Every table holding a user's private data carries `user_id`, and every
query against it is scoped to the current user (CLAUDE.md non-negotiable #1).
Shared tables (employers, postings) arrive in 0.3."""

import secrets
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Copied into the session cookie at login and compared on every request.
    # Rotating it (password reset, disabling the account) ends every session.
    session_token: Mapped[str] = mapped_column(String(64), default=new_session_token)
    is_admin: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by the admin (#3). A disabled account can't log in, and disabling it
    # rotates session_token, which ends any session it already has.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invite(Base):
    """A one-time set-password link (app/invites.py). Only the token's hash is stored."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimit(Base):
    """One counter per key per fixed window (app/rate_limit.py)."""

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    hits: Mapped[int]


class AiSpend(Base):
    """The AI spend ledger (#22): one row per model call that came back with a bill.
    Every call runs on Sean's key, so each user's month is summed from here for their
    cap, and everyone's for the key's (app/spend.py). Totals are never stored.

    user_id is SET NULL, not CASCADE: money spent stays spent, so deleting a user
    must not shrink the month's total. Cost is integer microcents (a millionth of a
    cent): a small call costs a fraction of a cent. purpose says what the call was
    for ("profile"), never what was in it: prompts and replies hold private data."""

    __tablename__ = "ai_spend"
    __table_args__ = (
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cache_read_tokens >= 0 AND cache_write_tokens >= 0",
            name="ai_spend_tokens",
        ),
        CheckConstraint("cost_microcents >= 0", name="ai_spend_cost"),
        # A user's month, and everyone's month: the two sums the cap needs.
        Index("ai_spend_user_month", "user_id", "created_at"),
        Index("ai_spend_month", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cache_read_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_microcents: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
