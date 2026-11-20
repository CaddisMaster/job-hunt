"""The aggregators (#45, plan §4): Adzuna and USAJobs, searched each night for the
jobs at employers the registry doesn't have.

Every search term is a **target title** from a profile its owner has confirmed (plan
§3), never assumed. The titles are pooled across users and deduplicated, so a search
says nothing about whose title it was, and nothing per user is logged or stored. This
module reads every active user's titles on purpose, the worker's one cross-user read
of private data, and keeps only the titles.

A source runs only with its keys set (`missing()`); blank means skipped, with the
reason in the crawl's summary. Each request first takes one from the source's daily
budget (`RateLimiter(…, 86400)`, in Postgres, so it holds across restarts). When it's
spent, the source stops until the next UTC day. An error stops that source for the
night: a bad key or a 429 won't fix itself on the next title.

A link on LinkedIn or Indeed is dropped before it's stored (non-negotiable 9).
"""

import logging
import time as clock
from dataclasses import dataclass, field
from datetime import datetime
from types import ModuleType

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import postings
from app.config import get_settings
from app.grounding import normalize
from app.models import Profile, User
from app.rate_limit import RateLimiter
from app.sources import FetchError, Found, adzuna, banned_link, get_json, usajobs

log = logging.getLogger("app.aggregators")

SOURCES: dict[str, ModuleType] = {"adzuna": adzuna, "usajobs": usajobs}
MAX_TITLES = 25  # titles searched a night, across everyone: the budgets' guard rail
BUDGET_KEY = "requests"  # the RateLimiter key under "aggregator:<source>" (tests change it)
DAY = 86_400


def target_titles(db: Session) -> list[str]:
    """Every confirmed profile's target titles, active users only, pooled and
    deduplicated (case and spacing ignored), in a stable order, at most MAX_TITLES."""
    rows = db.scalars(
        select(Profile.data)
        .join(User, User.id == Profile.user_id)
        .where(Profile.reviewed_at.is_not(None), User.disabled_at.is_(None))
    )
    titles: dict[str, str] = {}
    for data in rows:
        for title in (data or {}).get("target_titles") or []:
            if isinstance(title, str) and title.strip():
                titles.setdefault(normalize(title), " ".join(title.split())[:120])
    return [titles[key] for key in sorted(titles)][:MAX_TITLES]


@dataclass
class Outcome:
    label: str
    skipped: str | None = None
    error: str | None = None
    out_of_budget: bool = False
    complete: bool = True
    requests: int = 0
    dropped: int = 0  # LinkedIn or Indeed links
    counts: postings.Counts = field(default_factory=postings.Counts)

    @property
    def ran(self) -> bool:
        return self.skipped is None

    @property
    def failed(self) -> bool:
        """Ran, and an error left it with nothing."""
        return self.ran and self.error is not None and self.counts.seen == 0

    def summary(self) -> str:
        """Counts and reasons only, for the job run's summary."""
        if self.skipped:
            return f"{self.label} skipped: {self.skipped}"
        text = f"{self.label}: {self.requests} requests, {self.counts.seen} seen, {self.counts.new} new, {self.counts.closed} closed"
        if self.dropped:
            text += f", {self.dropped} LinkedIn/Indeed dropped"
        if self.error:
            text += f"; stopped: {self.error}"
        elif self.out_of_budget:
            text += "; stopped: daily request budget reached"
        elif not self.complete:
            text += "; some titles had more pages than are read"
        return text


def run(db: Session, source: str, titles: list[str], now: datetime) -> Outcome:
    """Search `source` for each title, store what it found (committed), and say how
    it went. Never raises for the source's own failures."""
    module = SOURCES[source]
    settings = get_settings()
    outcome = Outcome(module.LABEL)
    if reason := module.missing(settings):
        outcome.skipped = reason
        return outcome
    if not titles:
        outcome.skipped = "no confirmed target titles yet"
        return outcome

    budget = RateLimiter(f"aggregator:{source}", module.daily_budget(settings), DAY)
    found: dict[str, Found] = {}
    for title in titles:
        for page in range(1, module.MAX_PAGES + 1):
            if not budget.hit(BUDGET_KEY):
                outcome.out_of_budget = True
                break
            if outcome.requests:
                clock.sleep(module.PAUSE_SECONDS)
            outcome.requests += 1
            url, headers = module.request(title, page, settings)
            try:
                jobs, more = module.parse(get_json(url, headers or None), page)
            except FetchError as error:
                outcome.error = str(error)
                break
            for job in jobs:
                if banned_link(job.url):
                    outcome.dropped += 1
                else:
                    found.setdefault(job.external_id, job)
            if not more:
                break
        else:
            outcome.complete = False  # the page cap, with more still there
        if outcome.error or outcome.out_of_budget:
            outcome.complete = False
            break

    outcome.counts = postings.record_search(db, source, found.values(), now, complete=outcome.complete)
    db.commit()
    log.info("aggregator %s: %s", source, outcome.summary())
    return outcome
