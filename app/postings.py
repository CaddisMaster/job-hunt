"""Postings: the jobs the nightly crawl finds (#44, plan §4). Shared by every user.

`record()` stores one employer's fetch: a job seen before is updated (one row per
job per source, never a duplicate), a new one is added, and the employer's open
postings that weren't returned have `missed_fetches` counted up, closing at
CLOSE_AFTER. Only a *successful* fetch counts, so a board that's down for a few
nights doesn't close its jobs. A closed job that comes back is reopened.

Every fetch re-reads each posting's location with app/area.py, so a place added to
the table later fixes the postings it had left `unknown`.

`shown()` is what users may see: open, in the area (remote only when asked), and
not from a disabled employer. An `unknown` location is never shown; the admin sees
those location texts counted (`unknown_locations`), so the table grows from real
misses rather than guesses.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import area
from app.area import Where
from app.models import Employer, Posting
from app.sources import Found, ashby, greenhouse, lever

SOURCES = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
CLOSE_AFTER = 3  # successful fetches in a row that didn't return the job


@dataclass
class Counts:
    seen: int = 0
    new: int = 0
    closed: int = 0

    def __iadd__(self, other: Counts) -> Counts:
        self.seen += other.seen
        self.new += other.new
        self.closed += other.closed
        return self


def fetch(employer: Employer) -> list[Found]:
    """The employer's open jobs, from its hiring system (one request). FetchError
    if that went wrong."""
    return SOURCES[employer.system].fetch(employer.token)


def label(system: str) -> str:
    return SOURCES[system].LABEL


def _where(job: Found) -> tuple[Where, bool]:
    """The posting's area and remote flag. A job its source calls remote is `remote`
    wherever it's "based" (Lever's remote jobs still name a city), unless that's in
    the area: then it's in the area, and shown to everyone, as a hybrid job is."""
    where = area.classify(job.location, job.latitude, job.longitude)
    if job.remote and where is not Where.IN_AREA:
        where = Where.REMOTE
    return where, job.remote or where is Where.REMOTE


def record(db: Session, employer: Employer, found: Iterable[Found], now: datetime) -> Counts:
    """Store one successful fetch of `employer` (uncommitted): see the module docstring."""
    jobs = {job.external_id: job for job in found}  # a board listing a job twice is one job
    counts = Counts(seen=len(jobs))
    existing = {}
    if jobs:
        rows = db.scalars(
            select(Posting).where(Posting.source == employer.system, Posting.external_id.in_(list(jobs)))
        )
        existing = {posting.external_id: posting for posting in rows}
    for external_id, job in jobs.items():
        where, remote = _where(job)
        posting = existing.get(external_id)
        if posting is None:
            posting = Posting(source=employer.system, external_id=external_id, first_seen_at=now)
            db.add(posting)
            counts.new += 1
        posting.employer_id = employer.id
        posting.company = employer.name
        posting.title = job.title
        posting.url = job.url
        posting.location = job.location
        posting.area = where.value
        posting.latitude, posting.longitude = job.latitude, job.longitude
        posting.remote = remote
        posting.description = job.description
        posting.last_seen_at = now
        posting.missed_fetches = 0
        posting.closed_at = None

    missing = select(Posting).where(
        Posting.employer_id == employer.id,
        Posting.source == employer.system,
        Posting.closed_at.is_(None),
    )
    if jobs:
        missing = missing.where(Posting.external_id.not_in(list(jobs)))
    for posting in db.scalars(missing):
        posting.missed_fetches += 1
        if posting.missed_fetches >= CLOSE_AFTER:
            posting.closed_at = now
            counts.closed += 1
    db.flush()
    return counts


def shown(db: Session, *, include_remote: bool = False) -> list[Posting]:
    """What users may see (#47 narrows it to their titles): open, in the area or
    (when they've turned remote on) remote, never unknown, never from a disabled
    employer. Newest first."""
    places = [Where.IN_AREA.value, Where.REMOTE.value] if include_remote else [Where.IN_AREA.value]
    return list(
        db.scalars(
            select(Posting)
            .outerjoin(Employer, Employer.id == Posting.employer_id)
            .where(
                Posting.closed_at.is_(None),
                Posting.area.in_(places),
                or_(Posting.employer_id.is_(None), Employer.enabled),
            )
            .order_by(Posting.first_seen_at.desc(), Posting.id.desc())
        )
    )


def unknown_locations(db: Session, limit: int = 50) -> list[tuple[str, int]]:
    """Open postings whose location couldn't be placed, by location text, most common
    first: what app/data/places.csv is missing. Location text only, nothing else."""
    rows = db.execute(
        select(Posting.location, func.count())
        .where(Posting.area == Where.UNKNOWN.value, Posting.closed_at.is_(None))
        .group_by(Posting.location)
        .order_by(func.count().desc(), Posting.location)
        .limit(limit)
    )
    return [(location, int(count)) for location, count in rows]


def open_unknown_count(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(Posting).where(Posting.area == Where.UNKNOWN.value, Posting.closed_at.is_(None))
    )
