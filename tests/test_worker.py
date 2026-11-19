"""The worker (#41): due jobs run once, under a lock, and a failure stays contained.

Every test makes jobs with unique names and deletes their rows afterwards, so the
shared database stays parallel-safe. Times are built from today's date in
APP_TIMEZONE, so housekeeping's 90-day cut never touches them.
"""

import threading
import time as clock
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select

from app import worker
from app.config import get_settings
from app.db import SessionLocal
from app.models import JobRun
from app.worker import Job, Skip, latest_occurrence, run_due
from tests.conftest import login

TZ = get_settings().tz


def at(days_ago: int, hour: int, minute: int = 0) -> datetime:
    """A wall-clock time in APP_TIMEZONE, `days_ago` days back, as UTC."""
    day = datetime.now(TZ).date() - timedelta(days=days_ago)
    return datetime.combine(day, time(hour, minute), tzinfo=TZ).astimezone(UTC)


@pytest.fixture
def names():
    made: list[str] = []

    def _name() -> str:
        made.append(f"test-{uuid.uuid4().hex[:12]}")
        return made[-1]

    yield _name
    with SessionLocal() as db:
        db.execute(delete(JobRun).where(JobRun.job.in_(made)))
        db.commit()


def counting_job(name: str, at_time: time = time(2, 30), *, sleep: float = 0.0, summary: str = "done"):
    calls: list[datetime] = []

    def run(db) -> str:
        calls.append(datetime.now(UTC))
        clock.sleep(sleep)
        return summary

    return Job(name, at_time, run), calls


def runs(name: str) -> list[JobRun]:
    with SessionLocal() as db:
        return list(db.scalars(select(JobRun).where(JobRun.job == name).order_by(JobRun.started_at)))


def record(name: str, started: datetime, outcome: str | None = "ok") -> None:
    with SessionLocal() as db:
        finished = started + timedelta(seconds=5) if outcome else None
        db.add(JobRun(job=name, started_at=started, finished_at=finished, outcome=outcome))
        db.commit()


@pytest.mark.criterion(41, "A due job runs once")
def test_a_due_job_runs_once_and_records_its_outcome(names):
    job, calls = counting_job(names(), summary="3 postings, 1 new")
    record(job.name, at(1, 2, 31))  # last night's run

    assert run_due([job], at(0, 2, 0)) == {}  # before 02:30: not due
    assert run_due([job], at(0, 2, 31)) == {job.name: "ok"}
    assert run_due([job], at(0, 2, 32)) == {}
    assert run_due([job], at(0, 23, 59)) == {}

    assert len(calls) == 1
    today = runs(job.name)[-1]
    assert today.started_at == at(0, 2, 31)
    assert today.outcome == "ok" and today.summary == "3 postings, 1 new"
    assert today.finished_at >= today.started_at


@pytest.mark.criterion(41, "Two workers don't run the same job twice")
def test_two_workers_at_once_run_the_job_once(names):
    job, calls = counting_job(names(), sleep=0.5)
    now = at(0, 2, 31)
    start = threading.Barrier(2)
    outcomes: list[dict] = []

    def one_worker():
        start.wait()
        outcomes.append(run_due([job], now))

    threads = [threading.Thread(target=one_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert sorted(len(o) for o in outcomes) == [0, 1]
    assert len(runs(job.name)) == 1


def test_a_worker_that_waited_out_the_lock_doesnt_run_it_again(names):
    """The second check after the lock: a run that finished while this worker was
    deciding counts, so a late worker finds the job no longer due."""
    job, calls = counting_job(names())
    now = at(0, 2, 31)
    assert run_due([job], now) == {job.name: "ok"}
    assert run_due([job], now) == {}
    assert len(calls) == 1


@pytest.mark.criterion(41, "A missed night runs once")
def test_three_missed_nights_run_once(names):
    job, calls = counting_job(names())
    record(job.name, at(4, 2, 31))  # the last run before the worker went down

    # It starts again three nights later, mid-morning.
    assert run_due([job], at(0, 10, 0)) == {job.name: "ok"}
    assert run_due([job], at(0, 10, 1)) == {}
    assert len(calls) == 1
    assert len(runs(job.name)) == 2


def test_a_job_that_never_ran_runs_on_the_first_start(names):
    job, calls = counting_job(names())
    assert run_due([job], at(0, 10, 0)) == {job.name: "ok"}
    assert len(calls) == 1


@pytest.mark.criterion(41, "A failing job doesn't stop the worker")
def test_a_failing_job_is_recorded_and_the_next_still_runs(names):
    def explode(db):
        raise RuntimeError("posting 'Jane Doe, Senior Engineer' could not be parsed")

    failing = Job(names(), time(2, 30), explode)
    following, calls = counting_job(names())

    assert run_due([failing, following], at(0, 2, 31)) == {failing.name: "failed", following.name: "ok"}
    assert len(calls) == 1
    [failed] = runs(failing.name)
    assert failed.outcome == "failed" and failed.finished_at is not None
    # The class, never the message: a message can quote data, and /admin shows this.
    assert "RuntimeError" in failed.summary and "Jane" not in failed.summary


def test_a_job_can_skip_itself(names):
    def nothing_to_do(db):
        raise Skip("No employers enabled.")

    job = Job(names(), time(2, 30), nothing_to_do)
    assert run_due([job], at(0, 2, 31)) == {job.name: "skipped"}
    assert runs(job.name)[0].summary == "No employers enabled."


def test_a_run_the_worker_was_stopped_during_is_closed_as_interrupted(names):
    job, _ = counting_job(names())
    record(job.name, at(1, 2, 31), outcome=None)  # killed mid-run last night

    assert run_due([job], at(0, 2, 31)) == {job.name: "ok"}
    interrupted, today = runs(job.name)
    assert interrupted.outcome == "failed" and interrupted.summary == worker.INTERRUPTED
    assert today.outcome == "ok"


def test_latest_occurrence_is_the_last_time_the_clock_read_it():
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)  # 07:00 in New York
    assert latest_occurrence(time(2, 30), now, TZ) == datetime(2026, 11, 10, 7, 30, tzinfo=UTC)
    assert latest_occurrence(time(8, 0), now, TZ) == datetime(2026, 11, 9, 13, 0, tzinfo=UTC)


def test_latest_occurrence_survives_the_clocks_changing():
    # 2026-03-08: 02:30 doesn't exist in New York. It still gives one moment, that day.
    spring = latest_occurrence(time(2, 30), datetime(2026, 3, 8, 12, 0, tzinfo=UTC), TZ)
    assert spring.date().isoformat() == "2026-03-08"
    # 2026-11-01: 01:30 happens twice. The first one counts, so it runs once.
    autumn = latest_occurrence(time(1, 30), datetime(2026, 11, 1, 12, 0, tzinfo=UTC), TZ)
    assert autumn == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_housekeeping_deletes_runs_older_than_90_days(names):
    old, recent = names(), names()
    record(old, datetime.now(UTC) - timedelta(days=91))
    record(recent, datetime.now(UTC) - timedelta(days=89))
    with SessionLocal() as db:
        assert "deleted" in worker.housekeeping(db)
    assert runs(old) == [] and len(runs(recent)) == 1


def test_every_registered_job_has_a_unique_name():
    assert len({job.name for job in worker.JOBS}) == len(worker.JOBS) > 0


def test_once_exits_non_zero_when_a_job_failed(monkeypatch, names):
    def explode(db):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "JOBS", (Job(names(), time(0, 0), explode),))
    assert worker.main(["--once"]) == 1
    monkeypatch.setattr(worker, "JOBS", (counting_job(names(), time(0, 0))[0],))
    assert worker.main(["--once"]) == 0


@pytest.mark.criterion(41, "The admin sees the last runs")
def test_the_admin_sees_each_jobs_last_run(client, make_user, monkeypatch, names):
    ran, never = counting_job(names(), summary="12 postings, 3 new")[0], counting_job(names(), time(4, 0))[0]
    monkeypatch.setattr(worker, "JOBS", (ran, never))
    record(ran.name, at(2, 2, 31))
    record(ran.name, at(1, 2, 31), outcome="failed")

    admin = make_user(is_admin=True)
    login(client, admin.email)
    page = client.get("/admin").text

    assert "Scheduled jobs" in page
    last = at(1, 2, 31).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    row = page[page.index(ran.name) : page.index("</tr>", page.index(ran.name))]
    assert last in row and "failed" in row and "02:30" in row
    never_row = page[page.index(never.name) : page.index("</tr>", page.index(never.name))]
    assert "never run" in never_row and "04:00" in never_row
