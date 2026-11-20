"""Web-driven Claude Code runs (#88): a button queues a run, and the runner on the VM
does it while Sean answers on the job's page.

The web app can't reach the VM, so the VM asks: the runner claims the oldest queued
run through the bridge, starts Claude Code headless, and finishes the run. While it
works, the run's tools post `progress` lines and **prompts** (a question, a hand-off to
the browser, the filled form ready to submit) that wait for Sean's answer here.

- **Only two kinds:** `apply` for one of his saved jobs, and `profile`. No free text
  ever reaches the VM from a button; the runner builds the command from the kind.
- **One at a time:** nothing is claimed while a run of his is running. A run left
  `running` longer than MAX_RUN (the runner died) is failed, so it can't block the queue.
- **Submit is Sean's click** (non-negotiable 8): a `submit` prompt's answer is
  "submit" only from his **Submit application** button, after he's seen the filled form.
- **Stop** marks the run and cancels whatever it's waiting for; a prompt unanswered
  for PROMPT_TIMEOUT does the same.

⚠️ Private data: every function takes the current user, or a run already found
through them, and touches only their rows (non-negotiable 1).
"""

import base64
import binascii
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Run, RunEvent, SavedJob, User

KINDS = ("apply", "profile")
PROMPTS = ("question", "browser", "submit")
ACTIVE = ("queued", "running")
ENDINGS = ("done", "failed", "stopped")
PROMPT_TIMEOUT = timedelta(minutes=30)
MAX_RUN = timedelta(hours=2)
TEXT_MAX = 4000
ANSWER_MAX = 2000
SUMMARY_MAX = 2000
SCREENSHOT_MAX = 5 * 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n"


class Invalid(ValueError):
    """Why a run request can't be done, worded for whoever asked."""


def _now() -> datetime:
    return datetime.now(UTC)


# ---- Queueing and claiming -----------------------------------------------------------------


def queue(db: Session, user: User, kind: str, job: SavedJob | None = None) -> Run:
    """Queue a run (uncommitted), or return the one already queued or running for the
    same thing: pressing the button twice is one run."""
    if kind not in KINDS:
        raise Invalid("Only Apply and Profile runs can be queued.")
    if (kind == "apply") != (job is not None):
        raise Invalid("An Apply run needs a saved job; a Profile run doesn't take one.")
    if job is not None and job.user_id != user.id:
        raise HTTPException(status_code=404)
    same_job = Run.saved_job_id == job.id if job is not None else Run.saved_job_id.is_(None)
    existing = db.scalar(select(Run).where(Run.user_id == user.id, Run.kind == kind, same_job, Run.status.in_(ACTIVE)))
    if existing is not None:
        return existing
    run = Run(user_id=user.id, kind=kind, saved_job_id=job.id if job else None, status="queued")
    db.add(run)
    db.flush()
    return run


def claim(db: Session, user: User) -> Run | None:
    """The runner's next run (uncommitted): the oldest queued one, marked running, or
    None while one is already running. A run running past MAX_RUN is failed first."""
    now = _now()
    db.execute(
        update(Run)
        .where(Run.user_id == user.id, Run.status == "running", Run.started_at < now - MAX_RUN)
        .values(status="failed", finished_at=now, summary="The run took more than two hours, so it was stopped.")
    )
    if db.scalar(select(Run.id).where(Run.user_id == user.id, Run.status == "running").limit(1)) is not None:
        return None
    run = db.scalar(
        select(Run).where(Run.user_id == user.id, Run.status == "queued").order_by(Run.created_at, Run.id).limit(1).with_for_update(skip_locked=True)  # fmt: skip
    )
    if run is None:
        return None
    run.status, run.started_at = "running", now
    db.flush()
    return run


def abandon(db: Session, user: User) -> int:
    """The runner restarted: anything of the user's still running didn't finish
    (uncommitted). The number failed."""
    rows = db.scalars(select(Run).where(Run.user_id == user.id, Run.status == "running")).all()
    for run in rows:
        _end(db, run, "failed", "The runner restarted before the run finished.")
    return len(rows)


def get(db: Session, user: User, run_id: int) -> Run:
    run = db.scalar(select(Run).where(Run.id == run_id, Run.user_id == user.id))
    if run is None:
        raise HTTPException(status_code=404)
    return run


def latest_for_job(db: Session, user: User, job: SavedJob) -> Run | None:
    return db.scalar(
        select(Run).where(Run.user_id == user.id, Run.saved_job_id == job.id).order_by(Run.created_at.desc(), Run.id.desc()).limit(1)  # fmt: skip
    )


def latest_profile_run(db: Session, user: User) -> Run | None:
    return db.scalar(
        select(Run).where(Run.user_id == user.id, Run.kind == "profile").order_by(Run.created_at.desc(), Run.id.desc()).limit(1)  # fmt: skip
    )


def events(db: Session, run: Run) -> list[RunEvent]:
    return list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id)))


def waiting(db: Session, run: Run) -> RunEvent | None:
    """The prompt the run is waiting on, if any."""
    return db.scalar(
        select(RunEvent)
        .where(RunEvent.run_id == run.id, RunEvent.kind.in_(PROMPTS), RunEvent.answered_at.is_(None), RunEvent.cancelled.is_(False))  # fmt: skip
        .order_by(RunEvent.id.desc())
        .limit(1)
    )


# ---- What the run says, through the bridge -------------------------------------------------


def _running(run: Run) -> None:
    if run.status != "running":
        raise Invalid(f"That run is {run.status}, not running.")


def progress(db: Session, run: Run, text: str) -> RunEvent:
    _running(run)
    text = (text or "").strip()
    if not text:
        raise Invalid("Say what's happening.")
    event = RunEvent(run_id=run.id, kind="progress", text=text[:TEXT_MAX])
    db.add(event)
    db.flush()
    return event


def ask(db: Session, run: Run, kind: str, text: str, options: list | None = None, screenshot_b64: str | None = None) -> RunEvent:
    """Post a prompt for Sean (uncommitted). A `submit` prompt needs the filled form's
    screenshot; only one prompt waits at a time."""
    _running(run)
    if run.stop_requested:
        raise Invalid("Sean pressed Stop: finish the run as stopped.")
    if kind not in PROMPTS:
        raise Invalid("A prompt is a question, browser or submit.")
    text = (text or "").strip()
    if not text:
        raise Invalid("Say what you need from Sean.")
    choices = [str(o).strip()[:200] for o in (options or []) if str(o).strip()][:20] if kind == "question" else []
    screenshot = None
    if screenshot_b64:
        try:
            screenshot = base64.b64decode(screenshot_b64, validate=True)
        except (binascii.Error, ValueError):
            raise Invalid("screenshot isn't base64.") from None
        if not screenshot.startswith(PNG) or len(screenshot) > SCREENSHOT_MAX:
            raise Invalid("screenshot must be a PNG under 5 MB.")
    if kind == "submit" and screenshot is None:
        raise Invalid("A submit prompt needs the filled form's screenshot, for Sean to check.")
    for open_prompt in db.scalars(
        select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.kind.in_(PROMPTS), RunEvent.answered_at.is_(None), RunEvent.cancelled.is_(False))  # fmt: skip
    ):
        open_prompt.cancelled = True  # a new prompt replaces one the run gave up on
    event = RunEvent(run_id=run.id, kind=kind, text=text[:TEXT_MAX], options=choices, screenshot=screenshot)
    db.add(event)
    db.flush()
    return event


def state(db: Session, run: Run, event: RunEvent) -> dict:
    """Where a prompt stands, for the run's polling tool (uncommitted: a timed-out
    prompt is cancelled and the run marked to stop)."""
    if event.answered_at is not None:
        return {"state": "answered", "answer": event.answer}
    if event.cancelled or run.stop_requested:
        return {"state": "stopped", "answer": None}
    if event.created_at < _now() - PROMPT_TIMEOUT:
        event.cancelled = True
        run.stop_requested = True
        db.add(RunEvent(run_id=run.id, kind="progress", text="Nobody answered for 30 minutes, so the run is stopping."))
        db.flush()
        return {"state": "timeout", "answer": None}
    return {"state": "waiting", "answer": None}


def _end(db: Session, run: Run, status: str, summary: str) -> None:
    run.status, run.finished_at = status, _now()
    run.summary = (summary or "").strip()[:SUMMARY_MAX]
    db.execute(
        update(RunEvent).where(RunEvent.run_id == run.id, RunEvent.answered_at.is_(None)).values(cancelled=True)
    )


def finish(db: Session, run: Run, status: str, summary: str) -> Run:
    if status not in ENDINGS:
        raise Invalid("A run finishes as done, failed or stopped.")
    if run.status in ENDINGS:
        return run  # already finished (Stop, or the runner's own wrap-up): the first word stands
    _end(db, run, status, summary)
    db.flush()
    return run


# ---- What Sean does, on the web page -------------------------------------------------------


def answer(db: Session, run: Run, event: RunEvent, value: str) -> RunEvent:
    """Sean's answer to the prompt the run is waiting on (uncommitted). For a submit
    prompt it's "submit" or "stop"; for the browser, "done"."""
    if event.run_id != run.id:
        raise HTTPException(status_code=404)
    if run.status != "running" or event.answered_at is not None or event.cancelled:
        raise Invalid("That isn't waiting for an answer any more.")
    value = (value or "").strip()
    if event.kind == "submit" and value not in ("submit", "stop"):
        raise Invalid("Submit or stop.")
    if event.kind == "browser":
        value = "done"
    if not value:
        raise Invalid("Type an answer.")
    if len(value) > ANSWER_MAX:
        raise Invalid(f"Keep it under {ANSWER_MAX} characters.")
    event.answer, event.answered_at = value, _now()
    if event.kind == "submit" and value == "stop":
        run.stop_requested = True
    db.flush()
    return event


def stop(db: Session, run: Run) -> Run:
    """Sean's Stop (uncommitted). A queued run ends now; a running one is told at its
    next wait, and whatever it's waiting on is cancelled."""
    if run.status == "queued":
        _end(db, run, "stopped", "Stopped before it started.")
    elif run.status == "running":
        run.stop_requested = True
        db.execute(
            update(RunEvent).where(RunEvent.run_id == run.id, RunEvent.answered_at.is_(None)).values(cancelled=True)
        )
    db.flush()
    return run
