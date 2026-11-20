"""The worker (#41): scheduled jobs, run outside the web process.

`python -m app.worker` is the `worker` compose service: a loop that wakes at the top
of each minute and runs every job that is due. `python -m app.worker --once` checks
once, runs what is due and exits, non-zero if anything failed (the release smoke test
uses it).

A job is registered below with a time of day in APP_TIMEZONE. It is **due** when that
time's latest occurrence has passed and no run of it has started since (job_runs).
So a job runs once a day, and after the droplet was down for three nights it runs
once on start, not three times. A brand-new job runs on the first start after it ships.

Each run holds a Postgres advisory lock named for its job, and re-checks that the job
is still due once it has it, so two workers (or a slow run and the next minute's
check) never run the same job twice. A job that raises is recorded as failed and
logged; the next job still runs, and the worker carries on.
"""

import argparse
import logging
import signal
import sys
import threading
import time as clock
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app import aggregators, employers, postings
from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import Employer, JobRun
from app.sources import FetchError

log = logging.getLogger("app.worker")

# pg_try_advisory_lock(namespace, key): the namespace keeps these locks apart from
# any other advisory lock the app takes later. The key is hashtext(job name).
LOCK_NAMESPACE = 41
SUMMARY_MAX = 500
INTERRUPTED = "Interrupted: the worker stopped during this run."


class Skip(Exception):
    """Raise from a job to record its run as skipped; the message is the summary."""


class Failed(Exception):
    """Raise from a job to record its run as failed with this summary (counts only),
    when it knows why and nothing needs the traceback."""


@dataclass(frozen=True)
class Job:
    name: str
    at: time  # a time of day in APP_TIMEZONE
    run: Callable[[Session], str]  # does the work, commits it, returns a summary (counts only)


def latest_occurrence(at: time, now: datetime, tz: ZoneInfo) -> datetime:
    """The most recent moment, at or before `now`, that the wall clock in `tz` read `at`."""
    local = now.astimezone(tz)
    candidate = datetime.combine(local.date(), at, tzinfo=tz)
    if candidate.astimezone(UTC) > now.astimezone(UTC):
        candidate = datetime.combine(local.date() - timedelta(days=1), at, tzinfo=tz)
    return candidate.astimezone(UTC)


def is_due(db: Session, job: Job, now: datetime, tz: ZoneInfo) -> bool:
    since = latest_occurrence(job.at, now, tz)
    started = db.scalar(select(JobRun.id).where(JobRun.job == job.name, JobRun.started_at >= since).limit(1))
    return started is None


def _execute(job: Job) -> tuple[str, str]:
    """Run the job's own work in its own session: (outcome, summary). Never raises."""
    with SessionLocal() as db:
        try:
            return "ok", job.run(db)
        except Skip as skip:
            db.rollback()
            return "skipped", str(skip)
        except Failed as failed:
            db.rollback()
            return "failed", str(failed)
        except Exception as error:
            db.rollback()
            log.exception("job %s failed", job.name)
            # The class only: a message can quote data (a row, a posting), and the
            # admin page shows this. The traceback is in the worker's log.
            return "failed", f"Failed: {type(error).__name__}. The worker's log has the details."


def run_job(job: Job, now: datetime, tz: ZoneInfo) -> str | None:
    """Run `job` if it is due and no one else is running it: its outcome, or None."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:ns, hashtext(:job))"), {"ns": LOCK_NAMESPACE, "job": job.name}):
            return None  # another worker has it
        try:
            with SessionLocal() as db:
                if not is_due(db, job, now, tz):
                    return None  # it ran while we waited for the lock
                # Holding the lock, so no run of this job is live: an unfinished row
                # is one the worker was stopped during.
                db.execute(
                    update(JobRun)
                    .where(JobRun.job == job.name, JobRun.finished_at.is_(None))
                    .values(finished_at=now, outcome="failed", summary=INTERRUPTED)
                )
                run = JobRun(job=job.name, started_at=now)
                db.add(run)
                db.commit()
                run_id = run.id
            log.info("job %s started", job.name)
            began = clock.monotonic()
            outcome, summary = _execute(job)
            with SessionLocal() as db:
                db.execute(
                    update(JobRun)
                    .where(JobRun.id == run_id)
                    .values(
                        finished_at=now + timedelta(seconds=clock.monotonic() - began),
                        outcome=outcome,
                        summary=summary[:SUMMARY_MAX],
                    )
                )
                db.commit()
            log.info("job %s %s: %s", job.name, outcome, summary)
            return outcome
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:ns, hashtext(:job))"), {"ns": LOCK_NAMESPACE, "job": job.name})


def run_due(jobs: Iterable[Job], now: datetime | None = None) -> dict[str, str]:
    """Run every due job, one after another: {job name: outcome} for those that ran.
    A job that can't even be started (the database is down) is logged and left for
    the next minute; the others still get their turn."""
    now = now or datetime.now(UTC)
    tz = get_settings().tz
    outcomes: dict[str, str] = {}
    for job in jobs:
        try:
            outcome = run_job(job, now, tz)
        except Exception:
            log.exception("could not run job %s; trying again next minute", job.name)
            outcomes[job.name] = "failed"
            continue
        if outcome is not None:
            outcomes[job.name] = outcome
    return outcomes


# ---- The jobs ---------------------------------------------------------------------

KEEP_RUNS = timedelta(days=90)


def housekeeping(db: Session) -> str:
    """Delete job runs older than KEEP_RUNS, so job_runs stays small."""
    deleted = db.execute(delete(JobRun).where(JobRun.started_at < datetime.now(UTC) - KEEP_RUNS)).rowcount
    db.commit()
    return f"{deleted} old job run{'' if deleted == 1 else 's'} deleted"


PAUSE_SECONDS = 1.0  # between employers: most share a host, and we're a guest there
ERROR_MAX = 300


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def crawl(db: Session) -> str:
    """Fetch every enabled employer's board (#44), one request each, then search the
    aggregators for everyone's confirmed target titles (#45, app/aggregators.py), and
    store what's there (app/postings.py). One employer's or source's failure is
    recorded, and the rest still run. The summary is counts and reasons only.

    Skipped when there's nothing to ask: no enabled employer and no aggregator with
    its keys set. Failed when everything that ran failed."""
    ids = [employer.id for employer in employers.to_fetch(db)]
    settings = get_settings()
    if not ids and all(source.missing(settings) for source in aggregators.SOURCES.values()):
        raise Skip("No enabled employers in the registry to fetch, and no aggregator keys are set.")
    totals, fetched, failed = postings.Counts(), 0, 0
    for i, employer_id in enumerate(ids):
        if i:
            clock.sleep(PAUSE_SECONDS)
        employer = db.get(Employer, employer_id)
        if employer is None or not employer.enabled:
            continue  # removed or disabled since the crawl started
        now = datetime.now(UTC)
        try:
            totals += postings.record(db, employer, postings.fetch(employer), now)
        except FetchError as error:
            db.rollback()
            reason = f"{postings.label(employer.system)} {error}"
        except Exception as error:
            db.rollback()
            log.exception("crawl: employer %s failed", employer_id)
            reason = f"Failed: {type(error).__name__}. The worker's log has the details."
        else:
            employer.last_fetched_at, employer.last_error = now, None
            db.commit()
            fetched += 1
            continue
        failed += 1
        employer = db.get(Employer, employer_id)
        if employer is not None:
            employer.last_error = f"{now.astimezone(settings.tz):%Y-%m-%d %H:%M}: {reason}"[:ERROR_MAX]
            db.commit()
    parts = []
    if ids:
        parts.append(
            f"{_plural(fetched, 'employer')} fetched, {failed} failed; {_plural(totals.seen, 'job')} seen, "
            f"{totals.new} new, {totals.closed} closed"
        )
    else:
        parts.append("No enabled employers")

    titles = aggregators.target_titles(db)
    searched, searches_ok = 0, 0
    for source in aggregators.SOURCES:
        try:
            outcome = aggregators.run(db, source, titles, datetime.now(UTC))
        except Exception as error:
            db.rollback()
            log.exception("crawl: aggregator %s failed", source)
            parts.append(f"{aggregators.SOURCES[source].LABEL} failed: {type(error).__name__}")
            searched += 1
            continue
        parts.append(outcome.summary())
        if outcome.ran:
            searched += 1
            searches_ok += not outcome.failed

    parts.append(f"{postings.open_unknown_count(db)} open with an unknown location")
    summary = "; ".join(parts)
    if not ids and not searched:
        raise Skip(summary)
    if fetched == 0 and searches_ok == 0:
        hint = " Each employer's reason is on the employer registry page." if ids else ""
        raise Failed(f"{summary}.{hint}")
    return summary


# Times are APP_TIMEZONE's wall clock. The database backup runs at 03:45, so nothing
# heavy is scheduled beside it.
JOBS: tuple[Job, ...] = (
    Job("crawl", time(2, 30), crawl),
    Job("housekeeping", time(3, 15), housekeeping),
)


# ---- The process ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description="Run the scheduled jobs.")
    parser.add_argument("--once", action="store_true", help="run what is due once, then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.once:
        outcomes = run_due(JOBS)
        log.info("ran %s", outcomes or "nothing: no job was due")
        return 1 if "failed" in outcomes.values() else 0

    # SIGTERM (docker stop) ends the wait between checks. A job already running
    # finishes first; if the stop timeout kills it instead, its row is closed as
    # interrupted on the job's next run.
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    settings = get_settings()
    log.info(
        "worker %s started; jobs: %s (%s)",
        settings.app_version,
        ", ".join(f"{job.name} at {job.at:%H:%M}" for job in JOBS),
        settings.app_timezone,
    )
    while not stop.is_set():
        run_due(JOBS)
        stop.wait(60 - clock.time() % 60)  # the top of the next minute
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
