"""ORM models.

⚠️ Every table holding a user's private data carries `user_id`, and every
query against it is scoped to the current user (CLAUDE.md non-negotiable #1).
Shared tables carry no `user_id`: job_runs, employers and postings."""

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
    """**Unused since 2026-11-20** (plan §10 decision 11: the AI work runs in Claude
    Code, so the app makes no Claude calls). The table shipped in v0.2.0, so it's
    dropped by a later release's migration, never in the one that stops using it
    (non-negotiable 5).

    The AI spend ledger (#22): one row per model call that came back with a bill.
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


class JobRun(Base):
    """One run of a scheduled job in the worker (#41, app/worker.py). Shared: no
    user's data. `summary` holds counts ("12 postings, 3 new"), never personal data.

    A row with no `finished_at` (and so no `outcome`) is a run in progress, or one the
    worker was stopped in the middle of; the next run of that job closes it as failed.
    Whether a job is due is read from here: it is due when its latest scheduled time
    has passed and no run of it has started since."""

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint("outcome IN ('ok', 'failed', 'skipped')", name="job_runs_outcome"),
        CheckConstraint("(finished_at IS NULL) = (outcome IS NULL)", name="job_runs_finished"),
        # A job's newest run: the due check and the admin page.
        Index("job_runs_job_newest", "job", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")


class SavedJob(Base):
    """A job its user pasted a link to (#63): discovery by the user. **Private** to
    that user (non-negotiable 1): every query is scoped to `user_id`. One row per user
    per link.

    `source` is how the posting was read: the page's schema.org JobPosting (page), or
    text the user pasted (text). `pending` means not read yet, or unreadable:
    `read_error` says why, and the job waits for its text. `description` is plain
    text, never markup."""

    __tablename__ = "saved_jobs"
    __table_args__ = (
        CheckConstraint(
            "source IN ('pending', 'page', 'text')", name="saved_jobs_source"
        ),
        CheckConstraint("char_length(description) <= 50000", name="saved_jobs_description_length"),
        UniqueConstraint("user_id", "url", name="saved_jobs_user_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    read_error: Mapped[str | None] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(200), default="", server_default="")
    title: Mapped[str] = mapped_column(String(300), default="", server_default="")
    location: Mapped[str] = mapped_column(String(500), default="", server_default="")
    remote: Mapped[bool] = mapped_column(default=False, server_default=false())
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TailoredResume(Base):
    """A resume tailored to one saved job (#73). **Private** to its user
    (non-negotiable 1); one per saved job, replaced when remade.

    `content` is what the PDF shows, every line already checked against the resume
    (non-negotiable 7); `left_out` is what the model wrote that the resume doesn't
    support, shown to the user and never printed. Only an approved one
    (`approved_at`) is used to apply."""

    __tablename__ = "tailored_resumes"
    __table_args__ = (
        UniqueConstraint("saved_job_id", name="tailored_resumes_saved_job"),
        Index("tailored_resumes_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    saved_job_id: Mapped[int] = mapped_column(ForeignKey("saved_jobs.id", ondelete="CASCADE"))
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    content: Mapped[dict] = mapped_column(JSONB)
    left_out: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiToken(Base):
    """A personal access token for the bridge (#74): how Claude Code calls the JSON
    API. **Private** to its user (non-negotiable 1). Only the SHA-256 of the token
    is stored; it's shown once. `prefix` tells tokens apart on the page."""

    __tablename__ = "api_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="api_tokens_hash"),
        Index("api_tokens_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(12))
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Application(Base):
    """What the /apply skill recorded for one saved job (#76). **Private**
    (non-negotiable 1); one per saved job, replaced if recorded again.

    `outcome` is `submitted` (Sean clicked Submit himself, non-negotiable 8) or
    `stopped` (`note` says why). `answers_used` is what was filled in, to check
    against the answer bank. `screenshot` is the confirmation page (PNG, ≤ 5 MB).
    A record written after the fact: the server never touches the employer's site."""

    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint("outcome IN ('submitted', 'stopped')", name="applications_outcome"),
        CheckConstraint("char_length(note) <= 4000", name="applications_note_length"),
        CheckConstraint("octet_length(screenshot) <= 5242880", name="applications_screenshot_size"),
        UniqueConstraint("saved_job_id", name="applications_saved_job"),
        Index("applications_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    saved_job_id: Mapped[int] = mapped_column(ForeignKey("saved_jobs.id", ondelete="CASCADE"))
    outcome: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    answers_used: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    screenshot: Mapped[bytes | None] = mapped_column(LargeBinary)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SiteNote(Base):
    """What Claude learned about one site's application flow (#76), read at the start
    of the next application there. **Private**; one per host per user."""

    __tablename__ = "site_notes"
    __table_args__ = (
        CheckConstraint("char_length(note) <= 20000", name="site_notes_note_length"),
        UniqueConstraint("user_id", "host", name="site_notes_user_host"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    host: Mapped[str] = mapped_column(String(255))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Run(Base):
    """A web-driven Claude Code run (#88): queued by a button, claimed by the runner
    on the VM, finished as done, failed or stopped. **Private** (non-negotiable 1).
    `stop_requested` is Sean's Stop button, seen at the run's next wait."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("kind IN ('apply', 'profile')", name="runs_kind"),
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed', 'stopped')", name="runs_status"),
        CheckConstraint("(kind = 'apply') = (saved_job_id IS NOT NULL)", name="runs_apply_has_job"),
        CheckConstraint("char_length(summary) <= 2000", name="runs_summary_length"),
        Index("runs_user_status", "user_id", "status", "created_at"),
        Index("runs_saved_job", "saved_job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    saved_job_id: Mapped[int | None] = mapped_column(ForeignKey("saved_jobs.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    stop_requested: Mapped[bool] = mapped_column(default=False, server_default=false())
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    """What a run shows on the job's page (#88): a progress line, or a prompt
    (question, browser, submit) waiting for Sean's answer, or cancelled when the run
    stopped first. A submit prompt carries the filled form's screenshot."""

    __tablename__ = "run_events"
    __table_args__ = (
        CheckConstraint("kind IN ('progress', 'question', 'browser', 'submit')", name="run_events_kind"),
        CheckConstraint("char_length(text) <= 4000", name="run_events_text_length"),
        CheckConstraint("char_length(answer) <= 2000", name="run_events_answer_length"),
        CheckConstraint("octet_length(screenshot) <= 5242880", name="run_events_screenshot_size"),
        Index("run_events_run", "run_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    options: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    screenshot: Mapped[bytes | None] = mapped_column(LargeBinary)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
