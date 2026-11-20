"""The aggregators (#45): Adzuna and USAJobs, searched for confirmed target titles.

Never the network: `sources._get` answers from tests/fixtures/sources/. Each test's
jobs carry its own token in their ids, its budget has its own key, and its titles are
its own, so tests running in parallel share nothing they assert on.
"""

import json
import logging
import random
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import delete, select

from app import aggregators, postings, sources, worker
from app.config import get_settings
from app.db import SessionLocal
from app.models import Employer, Posting, Profile
from app.sources import FetchError, adzuna, banned_link, usajobs
from app.worker import Failed, Skip
from tests.conftest import login

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
KEYS = {"adzuna_app_id": "id-test", "adzuna_app_key": "key-test-secret", "usajobs_api_key": "usa-test-secret", "usajobs_user_agent": "someone@example.com"}  # fmt: skip


class Api:
    """Stands in for both APIs. A request for one of `titles` answers that source's
    fixture (or `answers[source]`: a payload, or a FetchError to raise); any other
    title, from another test's profile, finds nothing."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.number = random.randrange(10**12, 10**13)  # USAJobs' numeric id, unique to the test
        self.titles: set[str] = set()
        self.answers: dict[str, object] = {}
        self.asked: list[tuple[str, str, int, dict]] = []  # (source, title, page, headers)

    def payload(self, source: str) -> object:
        text = (FIXTURES / f"{source}.json").read_text().replace("TOKEN", self.token)
        return json.loads(text.replace("99000000", str(self.number)))

    def get(self, url: str, headers: dict | None = None) -> bytes:
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.hostname == "api.adzuna.com":
            source, title, page = "adzuna", query["what"], int(parts.path.rsplit("/", 1)[1])
        elif parts.hostname == "data.usajobs.gov":
            source, title, page = "usajobs", query["Keyword"], int(query["Page"])
        else:
            raise FetchError("answered 404")
        if title not in self.titles:
            return json.dumps({"results": [], "count": 0} if source == "adzuna" else {"SearchResult": {"SearchResultCountAll": 0, "SearchResultItems": []}}).encode()  # fmt: skip
        self.asked.append((source, title, page, headers or {}))
        answer = self.answers.get(source, self.payload(source))
        if callable(answer):
            answer = answer(page)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer).encode()

    def sources_asked(self) -> set[str]:
        return {source for source, *_ in self.asked}


@pytest.fixture
def api(monkeypatch):
    fake = Api(f"t{uuid.uuid4().hex[:12]}")
    settings = get_settings()
    for key, value in KEYS.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(sources, "_get", fake.get)
    monkeypatch.setattr(aggregators, "BUDGET_KEY", f"test-{fake.token}")
    monkeypatch.setattr(adzuna, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(usajobs, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(worker.employers, "to_fetch", lambda db: [])  # the boards are test_crawl.py's
    yield fake
    with SessionLocal() as db:
        ids = [f"{fake.token}-1", f"{fake.token}-2", f"{fake.token}-3", str(fake.number), f"{fake.token}-x"]
        db.execute(delete(Posting).where(Posting.source.in_(["adzuna", "usajobs"]), Posting.external_id.in_(ids)))
        db.commit()


@pytest.fixture
def confirmed(make_user, api):
    """A user with a confirmed profile whose one target title is the test's own."""

    def _make(title: str | None = None, *, reviewed: bool = True, disabled: bool = False) -> str:
        title = title or f"Data Analyst {api.token}"
        user = make_user()
        with SessionLocal() as db:
            now = datetime.now(UTC)
            db.add(Profile(user_id=user.id, data={"target_titles": [title]}, model="test", reviewed_at=now if reviewed else None))
            if disabled:
                db.get(type(user), user.id).disabled_at = now
            db.commit()
        api.titles.add(title)
        return title

    return _make


def crawl() -> str:
    with SessionLocal() as db:
        return worker.crawl(db)


def run(source: str, titles: list[str]) -> aggregators.Outcome:
    with SessionLocal() as db:
        return aggregators.run(db, source, titles, datetime.now(UTC))


def stored(api: Api) -> dict[str, Posting]:
    with SessionLocal() as db:
        rows = db.scalars(select(Posting).where(Posting.source.in_(["adzuna", "usajobs"]), Posting.external_id.like(f"{api.token}%") | (Posting.external_id == str(api.number))))  # fmt: skip
        found = {f"{p.source}:{p.title}": p for p in rows}
        db.expunge_all()
        return found


# ---- Searching ---------------------------------------------------------------------


@pytest.mark.criterion(45, "Aggregator jobs are fetched for confirmed target titles")
def test_both_aggregators_are_searched_for_confirmed_titles(api, confirmed):
    title = confirmed()
    summary = crawl()
    assert {(s, t) for s, t, *_ in api.asked} == {("adzuna", title), ("usajobs", title)}
    jobs = stored(api)
    analyst = jobs["adzuna:Data Analyst"]
    assert analyst.employer_id is None and analyst.company == "Allegheny Mutual"
    assert analyst.area == "in_area" and analyst.url.startswith("https://www.adzuna.com/")
    assert analyst.description == "Analyze claims data for a Pittsburgh insurer…"
    assert jobs["adzuna:Senior Data Analyst"].area == "in_area"  # Cranberry Township, by name
    assert jobs["adzuna:Senior Data Analyst"].location == "Cranberry Township, Pennsylvania"
    assert jobs["adzuna:Data Analyst II"].area == "out_of_area"
    federal = jobs["usajobs:Data Analyst"]
    assert federal.company == "Veterans Health Administration"
    assert federal.url == f"https://www.usajobs.gov/job/{api.token}-1"
    assert federal.area == "in_area" and federal.latitude == pytest.approx(40.4406)
    assert federal.location == "Washington, District of Columbia; Pittsburgh, Pennsylvania"
    assert federal.description == "Analyze patient outcome data.\n\nOne year of specialized experience…"
    assert jobs["usajobs:Data Analyst (Remote)"].area == "remote"
    assert "Adzuna: " in summary and "USAJobs: " in summary


def test_the_requests_carry_the_keys_where_each_api_wants_them(api, confirmed):
    title = confirmed()
    crawl()
    usa = next(headers for source, _, _, headers in api.asked if source == "usajobs")
    assert usa == {"User-Agent": "someone@example.com", "Authorization-Key": "usa-test-secret"}
    url, headers = adzuna.request(title, 1, get_settings())
    query = parse_qs(urlsplit(url).query)
    assert url.startswith("https://api.adzuna.com/v1/api/jobs/us/search/1?")
    assert query["app_key"] == ["key-test-secret"] and query["distance"] == ["50"] and headers == {}


def test_only_confirmed_profiles_of_active_users_give_titles(api, confirmed):
    mine = confirmed()
    draft = confirmed(f"Draft Title {api.token}", reviewed=False)
    gone = confirmed(f"Gone Title {api.token}", disabled=True)
    spaced = confirmed(f"  data   ANALYST {api.token} ")  # the same title, typed differently
    with SessionLocal() as db:
        titles = aggregators.target_titles(db)
    assert mine in titles and draft not in titles and gone not in titles
    assert sum(t.casefold().split() == spaced.casefold().split() for t in titles) == 1


def test_titles_are_capped_each_night(api, monkeypatch, confirmed):
    confirmed()
    monkeypatch.setattr(aggregators, "MAX_TITLES", 1)
    with SessionLocal() as db:
        assert len(aggregators.target_titles(db)) == 1


def test_pages_are_read_until_there_are_no_more(api):
    title = f"Paged {api.token}"
    api.titles.add(title)

    def page(n):
        payload = api.payload("adzuna")
        payload["count"] = 120  # three pages of 50
        for job in payload["results"]:
            job["id"] = f"{api.token}-x" if n > 1 else job["id"]
        return payload

    api.answers["adzuna"] = page
    outcome = run("adzuna", [title])
    assert [p for s, _, p, _ in api.asked] == [1, 2, 3] and outcome.complete


def test_the_page_cap_leaves_a_search_incomplete(api, monkeypatch):
    title = f"Capped {api.token}"
    api.titles.add(title)
    monkeypatch.setattr(adzuna, "MAX_PAGES", 1)
    payload = api.payload("adzuna")
    payload["count"] = 500
    api.answers["adzuna"] = payload
    outcome = run("adzuna", [title])
    assert not outcome.complete and "more pages" in outcome.summary()


# ---- Keys, budgets, failures ---------------------------------------------------------


@pytest.mark.criterion(45, "No key means the source is skipped")
def test_a_source_without_its_key_is_skipped_and_says_why(api, confirmed, monkeypatch):
    confirmed()
    monkeypatch.setattr(get_settings(), "adzuna_app_key", "")
    summary = crawl()
    assert "Adzuna skipped: no ADZUNA_APP_ID or ADZUNA_APP_KEY" in summary
    assert api.sources_asked() == {"usajobs"}


def test_with_no_keys_and_no_employers_the_crawl_is_skipped(api, monkeypatch):
    for key in KEYS:
        monkeypatch.setattr(get_settings(), key, "")
    with pytest.raises(Skip, match="no aggregator keys are set"):
        crawl()


def test_with_keys_but_no_confirmed_titles_a_source_says_so(api):
    outcome = run("adzuna", [])
    assert not outcome.ran and outcome.summary() == "Adzuna skipped: no confirmed target titles yet"
    assert api.asked == []


@pytest.mark.criterion(45, "The daily request budget holds")
def test_the_daily_budget_stops_a_source_until_tomorrow(api, monkeypatch):
    titles = [f"Budget {n} {api.token}" for n in range(3)]
    api.titles.update(titles)
    monkeypatch.setattr(get_settings(), "adzuna_daily_requests", 2)
    first = run("adzuna", titles)
    assert first.requests == 2 and first.out_of_budget and not first.complete
    assert "daily request budget reached" in first.summary()
    again = run("adzuna", titles)
    assert again.requests == 0 and again.out_of_budget
    assert len(api.asked) == 2
    # The budget is a day long, counted in Postgres (app/rate_limit.py).
    limiter = aggregators.RateLimiter("aggregator:adzuna", 2, aggregators.DAY)
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    assert limiter.hit(aggregators.BUDGET_KEY, now=tomorrow)


def test_an_error_stops_that_source_for_the_night_and_the_other_still_runs(api, confirmed):
    confirmed()
    api.answers["adzuna"] = FetchError("answered 401")
    summary = crawl()
    # Other tests' titles may be searched first (and find nothing), so the count varies.
    assert re.search(r"Adzuna: \d+ requests, 0 seen, 0 new, 0 closed; stopped: answered 401", summary)
    assert "usajobs:Data Analyst" in stored(api)


def test_when_everything_that_ran_failed_the_run_failed(api, confirmed):
    confirmed()
    api.answers["adzuna"] = FetchError("answered 401")
    api.answers["usajobs"] = FetchError("answered 403")
    with pytest.raises(Failed, match="stopped: answered 403"):
        crawl()


def test_an_answer_in_an_unknown_shape_is_an_error_not_a_crash(api):
    title = f"Shape {api.token}"
    api.titles.add(title)
    api.answers["usajobs"] = {"SearchResult": "nope"}
    assert run("usajobs", [title]).error == "answered in a shape Job Hunt doesn't know"


def test_no_key_appears_in_the_summary_or_the_log(api, confirmed, caplog):
    confirmed()
    caplog.set_level(logging.DEBUG)
    summary = crawl()
    for secret in ("key-test-secret", "usa-test-secret", "someone@example.com"):
        assert secret not in summary and secret not in caplog.text
    # httpx2 logs each request's URL at INFO, and Adzuna's key is in the URL.
    assert logging.getLogger("httpx2").getEffectiveLevel() >= logging.WARNING


# ---- LinkedIn and Indeed ---------------------------------------------------------------


@pytest.mark.criterion(45, "LinkedIn and Indeed links are dropped")
def test_linkedin_and_indeed_links_are_dropped(api):
    title = f"Links {api.token}"
    api.titles.add(title)
    payload = api.payload("adzuna")
    payload["results"][0]["redirect_url"] = "https://www.linkedin.com/jobs/view/1"
    payload["results"][1]["redirect_url"] = "https://uk.indeed.com/viewjob?jk=2"
    api.answers["adzuna"] = payload
    outcome = run("adzuna", [title])
    assert outcome.dropped == 2 and "2 LinkedIn/Indeed dropped" in outcome.summary()
    assert set(stored(api)) == {"adzuna:Data Analyst II"}


@pytest.mark.parametrize(
    ("url", "banned"),
    [
        ("https://www.linkedin.com/jobs/view/1", True),
        ("https://linkedin.com/x", True),
        ("https://LNKD.IN/abc", True),
        ("https://www.indeed.com/viewjob", True),
        ("https://indeed.com/x", True),
        ("https://notlinkedin.com/x", False),
        ("https://www.adzuna.com/land/ad/1", False),
        ("https://www.usajobs.gov/job/1", False),
    ],
)
def test_banned_link(url, banned):
    assert banned_link(url) is banned


# ---- Closing ---------------------------------------------------------------------------


def _stale_posting(api: Api, hours: float) -> int:
    with SessionLocal() as db:
        then = datetime.now(UTC) - timedelta(hours=hours)
        posting = Posting(source="adzuna", external_id=f"{api.token}-x", company="Old Co", title="Old job", url="https://www.adzuna.com/x", area="in_area", first_seen_at=then, last_seen_at=then)  # fmt: skip
        db.add(posting)
        db.commit()
        return posting.id


def test_a_complete_search_closes_jobs_unseen_for_three_nights(api):
    old = _stale_posting(api, hours=61)
    with SessionLocal() as db:
        counts = postings.record_search(db, "adzuna", [], datetime.now(UTC), complete=True)
        db.commit()
        assert db.get(Posting, old).closed_at is not None
    assert counts.closed >= 1


def test_a_search_cut_short_closes_nothing(api):
    old = _stale_posting(api, hours=100)
    with SessionLocal() as db:
        counts = postings.record_search(db, "adzuna", [], datetime.now(UTC), complete=False)
        db.commit()
    assert counts.closed == 0
    assert old


def test_a_job_seen_again_is_one_posting_and_reopens(api):
    title = f"Again {api.token}"
    api.titles.add(title)
    run("adzuna", [title])
    with SessionLocal() as db:
        posting = db.scalar(select(Posting).where(Posting.source == "adzuna", Posting.external_id == f"{api.token}-1"))
        posting.closed_at = datetime.now(UTC)
        db.commit()
    run("adzuna", [title])
    jobs = stored(api)
    assert len(jobs) == 3 and jobs["adzuna:Data Analyst"].closed_at is None


# ---- The admin's suggestions -----------------------------------------------------------


def test_employers_the_aggregators_keep_finding_are_suggested(api, client, make_user):
    name = f"Frequent Co {api.token}"
    title = f"Suggest {api.token}"
    api.titles.add(title)
    payload = api.payload("adzuna")
    for job in payload["results"]:
        job["company"]["display_name"] = name
        job["latitude"], job["longitude"] = 40.44, -79.99
    api.answers["adzuna"] = payload
    run("adzuna", [title])
    with SessionLocal() as db:
        assert (name, 3) in postings.suggested_employers(db, limit=10_000)
        employer = Employer(name=name.upper(), system="greenhouse", token=f"t-{api.token}", careers_url="https://x/y")
        db.add(employer)
        db.commit()
        try:
            assert name not in dict(postings.suggested_employers(db, limit=10_000))
        finally:
            db.delete(employer)
            db.commit()

    assert login(client, make_user(is_admin=True).email).status_code == 303
    page = client.get("/admin/employers").text
    assert "Employers the aggregators keep finding" in page
    assert f"<td>{name}</td><td>3</td>" in page
