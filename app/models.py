"""ORM models.

⚠️ Every table holding a user's private data carries `user_id`, and every
query against it is scoped to the current user (CLAUDE.md non-negotiable #1).
Shared tables (employers, postings) arrive in 0.3."""

import secrets
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class Resume(Base):
    """An uploaded resume (#23): private to its owner (non-negotiable 1).

    The file itself is in Postgres (bytea), not on a volume, so the nightly dump and
    its encryption on the VM (#21) cover resumes with nothing else to back up. It is
    at most 5 MB (app/resumes.py), and a user has a few. The newest is the base
    resume; older ones are kept. `filename` is only ever displayed, never used as a
    path. `text` is what extraction got out of it, empty when the PDF has no text
    layer. Deleting the user deletes their resumes.
    """

    __tablename__ = "resumes"
    __table_args__ = (
        CheckConstraint("size_bytes > 0 AND size_bytes <= 5242880", name="resumes_size"),
        CheckConstraint("kind IN ('pdf', 'docx')", name="resumes_kind"),
        Index("resumes_user_newest", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(8))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Profile(Base):
    """The structured profile built from a resume (#24), one per user, private to them.

    `data` is the extraction (app/profile.py's ProfileData): roles, skills,
    education, certifications, seniority, years and target titles, each title and
    skill with the resume line it came from. Search targets come only from here,
    never assumed (plan §3). Later steps use a profile only once `reviewed_at` is
    set: its owner has checked and confirmed it.
    """

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    # The resume it was built from; kept if that resume is ever deleted.
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    data: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64))
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Answer(Base):
    """One answer in a user's answer bank (#25): what applications ask that a resume
    doesn't say (plan §3). Private to its owner.

    `key` names the question. The starter questions are defined in app/answers.py; a
    question added later (from 0.5's drafting, once its owner approves the answer)
    carries its own wording in `question`. No row, or an empty answer, means
    unanswered: it is never guessed (non-negotiable 7)."""

    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("user_id", "key", name="answers_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
