"""The nightly crawl (#44): Greenhouse, Lever and Ashby boards into postings.

Never the network: `sources._get` answers from tests/fixtures/sources/, each test's
board has its own token (and so its own posting ids), and the crawl only sees the
employers the test made, so the shared database stays parallel-safe.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app import postings, sources, worker
from app.db import SessionLocal
from app.models import Employer, Posting
from app.sources import FetchError, greenhouse, plain_text
from app.worker import Failed, Skip
from tests.conftest import login

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


def fixture(system: str, token: str) -> object:
    return json.loads((FIXTURES / f"{system}.json").read_text().replace("TOKEN", token))


class Boards:
    """Stands in for the three APIs: each test board answers its fixture, or what a
    test set for it (`answers[token]`: a payload, or a FetchError to raise)."""

    def __init__(self) -> None:
        self.systems: dict[str, str] = {}  # token → system, for the boards a test made
        self.answers: dict[str, object] = {}
        self.asked: list[str] = []

    def get(self, url: str) -> bytes:
        self.asked.append(url)
        for token, system in self.systems.items():
            if url == postings.SOURCES[system].url(token):
                answer = self.answers.get(token, fixture(system, token))
                if isinstance(answer, Exception):
                    raise answer
                return json.dumps(answer).encode()
        raise FetchError("answered 404")


@pytest.fixture
def boards(monkeypatch):
    api = Boards()
    made: list[int] = []

    def make(system: str = "greenhouse", *, enabled: bool = True, name: str = "Example Co") -> Employer:
        token = f"test-{uuid.uuid4().hex[:12]}"
        with SessionLocal() as db:
            employer = Employer(name=name, system=system, token=token, careers_url=f"https://x/{token}", enabled=enabled)
            db.add(employer)
            db.commit()
            db.refresh(employer)
            db.expunge(employer)
        api.systems[token] = system
        made.append(employer.id)
        return employer

    real_to_fetch = worker.employers.to_fetch
    monkeypatch.setattr(worker.employers, "to_fetch", lambda db: [e for e in real_to_fetch(db) if e.id in made])
    monkeypatch.setattr(sources, "_get", api.get)
    monkeypatch.setattr(worker, "PAUSE_SECONDS", 0)
    api.make = make
    yield api
    with SessionLocal() as db:
        db.execute(delete(Posting).where(Posting.employer_id.in_(made)))
        db.execute(delete(Employer).where(Employer.id.in_(made)))
        db.commit()


def crawl() -> str:
    with SessionLocal() as db:
        return worker.crawl(db)


def postings_of(employer: Employer) -> dict[str, Posting]:
    with SessionLocal() as db:
        rows = db.scalars(select(Posting).where(Posting.employer_id == employer.id))
        found = {posting.title: posting for posting in rows}
        db.expunge_all()
        return found


def reload(employer: Employer) -> Employer:
    with SessionLocal() as db:
        fresh = db.get(Employer, employer.id)
        db.expunge(fresh)
        return fresh


def shown_ids(*, include_remote: bool = False) -> set[int]:
    with SessionLocal() as db:
        return {posting.id for posting in postings.shown(db, include_remote=include_remote)}


# ---- Fetching ----------------------------------------------------------------------


@pytest.mark.criterion(44, "An employer's open jobs are fetched")
def test_an_employers_open_jobs_become_postings(boards):
    employer = boards.make("greenhouse")
    summary = crawl()
    found = postings_of(employer)
    assert set(found) == {"Staff Accountant", "Senior Accountant", "Payroll Specialist"}
    job = found["Staff Accountant"]
    assert job.source == "greenhouse" and job.external_id == f"{employer.token}-1001"
    assert job.company == "Example Co"
    assert job.url == f"https://boards.greenhouse.io/example/jobs/{employer.token}-1001"
    assert job.location == "Pittsburgh, PA" and job.area == "in_area"
    assert job.description == "About the role\n\nClose the books each month & reconcile accounts.\n\n• 3+ years in accounting\n\n• CPA a plus"
    assert found["Senior Accountant"].area == "in_area"  # New York or Pittsburgh
    assert found["Payroll Specialist"].area == "out_of_area"
    fresh = reload(employer)
    assert fresh.last_fetched_at is not None and fresh.last_error is None
    assert summary.startswith("1 employer fetched, 0 failed; 3 jobs seen, 3 new, 0 closed;")
    assert boards.asked == [greenhouse.url(employer.token)]  # one request per employer


def test_lever_jobs_are_read_with_their_lists_and_remote_flag(boards):
    employer = boards.make("lever")
    crawl()
    found = postings_of(employer)
    ap = found["Accounts Payable Specialist"]
    assert ap.location == "Cranberry Township, PA" and ap.area == "in_area" and not ap.remote
    assert ap.url == f"https://jobs.lever.co/example/{employer.token}-6ed76ce8-0001"
    assert ap.description == (
        "Join our finance team.\n\nWhat you'll do\n\n• Prepare journal entries\n\n• Reconcile accounts\n\n"
        "We offer health insurance."
    )
    remote = found["Revenue Accountant"]
    assert remote.remote and remote.area == "remote"  # remote, "based" in New York
    assert remote.id in shown_ids(include_remote=True)
    assert remote.id not in shown_ids()


def test_ashby_jobs_use_the_address_and_skip_unlisted_ones(boards):
    employer = boards.make("ashby")
    crawl()
    found = postings_of(employer)
    assert set(found) == {"Financial Analyst", "Controller", "Tax Manager"}  # "Hidden Role" is unlisted
    analyst = found["Financial Analyst"]
    assert analyst.location == "Pittsburgh Office; Pittsburgh, Pennsylvania" and analyst.area == "in_area"
    assert analyst.description == "Build the budget."  # the <style> block's contents are gone
    controller = found["Controller"]
    assert controller.remote and controller.area == "remote"
    assert controller.location == "Remote (US); Europe"
    hybrid = found["Tax Manager"]  # Ashby's isRemote is true for hybrid jobs too
    assert not hybrid.remote and hybrid.area == "out_of_area"


def test_disabled_employers_are_not_fetched_and_their_postings_not_shown(boards):
    employer = boards.make("greenhouse")
    crawl()
    ids = {posting.id for posting in postings_of(employer).values() if posting.area == "in_area"}
    assert ids and ids <= shown_ids()
    with SessionLocal() as db:
        db.get(Employer, employer.id).enabled = False
        db.commit()
    boards.asked.clear()
    with pytest.raises(Skip):
        crawl()  # nothing left to fetch
    assert boards.asked == []
    assert not ids & shown_ids()


def test_the_crawl_is_skipped_when_the_registry_is_empty(boards):
    with pytest.raises(Skip, match="No enabled employers"):
        crawl()


def test_the_crawl_is_a_nightly_job_before_the_backup():
    job = next(job for job in worker.JOBS if job.name == "crawl")
    assert job.run is worker.crawl
    assert job.at.strftime("%H:%M") == "02:30"


# ---- Seen again, and gone -----------------------------------------------------------


@pytest.mark.criterion(44, "A job seen again isn't duplicated")
def test_a_job_seen_again_is_one_posting_with_last_seen_updated(boards):
    employer = boards.make("greenhouse")
    crawl()
    first = postings_of(employer)
    summary = crawl()
    second = postings_of(employer)
    assert len(second) == 3
    for title, posting in second.items():
        assert posting.id == first[title].id
        assert posting.first_seen_at == first[title].first_seen_at
        assert posting.last_seen_at > first[title].last_seen_at
    assert "3 jobs seen, 0 new" in summary


def test_a_board_listing_a_job_twice_makes_one_posting(boards):
    employer = boards.make("greenhouse")
    payload = fixture("greenhouse", employer.token)
    payload["jobs"].append(payload["jobs"][0])
    boards.answers[employer.token] = payload
    crawl()
    assert len(postings_of(employer)) == 3


def without_payroll(token: str) -> object:
    payload = fixture("greenhouse", token)
    payload["jobs"] = [job for job in payload["jobs"] if job["title"] != "Payroll Specialist"]
    return payload


@pytest.mark.criterion(44, "A job that's gone is closed")
def test_a_job_missing_for_three_nights_is_closed_and_reopens_if_it_returns(boards):
    employer = boards.make("greenhouse")
    payload = fixture("greenhouse", employer.token)
    payload["jobs"][2]["location"] = {"name": "Wexford, PA"}  # so it's one users would see
    boards.answers[employer.token] = payload
    crawl()
    payroll = postings_of(employer)["Payroll Specialist"]
    assert payroll.id in shown_ids()

    boards.answers[employer.token] = without_payroll(employer.token)
    crawl()
    crawl()
    payroll = postings_of(employer)["Payroll Specialist"]
    assert payroll.missed_fetches == 2 and payroll.closed_at is None  # two nights isn't gone
    summary = crawl()
    payroll = postings_of(employer)["Payroll Specialist"]
    assert payroll.closed_at is not None  # kept, for history
    assert payroll.id not in shown_ids()
    assert "1 closed" in summary
    assert postings_of(employer)["Staff Accountant"].closed_at is None

    boards.answers[employer.token] = payload
    crawl()
    payroll = postings_of(employer)["Payroll Specialist"]
    assert payroll.closed_at is None and payroll.missed_fetches == 0
    assert payroll.id in shown_ids()


def test_a_board_that_fails_doesnt_close_its_jobs(boards):
    employer = boards.make("greenhouse")
    crawl()
    boards.answers[employer.token] = FetchError("answered 503")
    for _ in range(4):
        with pytest.raises(Failed):
            crawl()
    assert all(p.closed_at is None and p.missed_fetches == 0 for p in postings_of(employer).values())


# ---- Failure ------------------------------------------------------------------------


@pytest.mark.criterion(44, "One broken employer doesn't stop the crawl")
def test_one_broken_employer_doesnt_stop_the_others(boards):
    broken = boards.make("greenhouse", name="Broken Co")
    working = boards.make("lever")
    boards.answers[broken.token] = FetchError("answered 500")
    summary = crawl()  # still ok: one employer was fetched
    assert set(postings_of(working)) == {"Accounts Payable Specialist", "Revenue Accountant"}
    assert reload(working).last_error is None
    error = reload(broken).last_error
    assert error.endswith(": Greenhouse answered 500")
    assert reload(broken).last_fetched_at is None
    assert summary.startswith("1 employer fetched, 1 failed;")


def test_a_board_in_a_shape_we_dont_know_is_an_error_not_a_crash(boards):
    odd = boards.make("ashby")
    fine = boards.make("greenhouse")
    boards.answers[odd.token] = ["not", "a", "board"]
    crawl()
    assert reload(odd).last_error.endswith("Ashby answered in a shape Job Hunt doesn't know")
    assert len(postings_of(fine)) == 3


def test_an_unexpected_error_is_recorded_on_the_employer(boards, monkeypatch):
    employer = boards.make("greenhouse")
    monkeypatch.setattr(greenhouse, "parse", lambda payload: 1 / 0)
    with pytest.raises(Failed, match="0 employers fetched, 1 failed"):
        crawl()
    assert "ZeroDivisionError" in reload(employer).last_error


def test_a_crawl_where_every_employer_fails_is_a_failed_run(boards):
    employer = boards.make("greenhouse")
    boards.answers[employer.token] = FetchError("didn't answer within 20 s")
    with pytest.raises(Failed, match="registry page"):
        crawl()
    assert reload(employer).last_error.endswith("Greenhouse didn't answer within 20 s")


# ---- Unknown locations, and markup --------------------------------------------------


@pytest.mark.criterion(44, "Postings with an unknown location are counted for the admin")
def test_unknown_locations_are_stored_hidden_and_counted_for_the_admin(boards, client, make_user):
    employer = boards.make("greenhouse")
    place = f"Building {uuid.uuid4().hex[:6]} <script>x</script>"
    payload = fixture("greenhouse", employer.token)
    for job in payload["jobs"][:2]:
        job["location"] = {"name": place}
    boards.answers[employer.token] = payload
    summary = crawl()
    unknown = [p for p in postings_of(employer).values() if p.location == place]
    assert len(unknown) == 2 and all(p.area == "unknown" for p in unknown)
    assert not {p.id for p in unknown} & shown_ids(include_remote=True)
    assert "open with an unknown location" in summary
    with SessionLocal() as db:
        assert (place, 2) in postings.unknown_locations(db, limit=10_000)

    assert login(client, make_user(is_admin=True).email).status_code == 303
    page = client.get("/admin").text
    assert "Locations Job Hunt couldn&#39;t place" in page or "Locations Job Hunt couldn't place" in page
    escaped = place.replace("<", "&lt;").replace(">", "&gt;")
    assert f"<td>{escaped}</td><td>2</td>" in page
    assert "<script>x</script>" not in page


@pytest.mark.criterion(44, "A description can't inject markup")
def test_a_description_is_stored_as_plain_text(boards):
    employer = boards.make("greenhouse")
    payload = fixture("greenhouse", employer.token)
    # A real <script> element, and the words "<script>" written in the text.
    payload["jobs"][0]["content"] = (
        "&lt;p&gt;Hello&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;"
        "&lt;p onclick=&quot;x()&quot;&gt;Write &amp;lt;script&amp;gt; tags&lt;/p&gt;"
        "&lt;img src=x onerror=alert(2)&gt;&lt;iframe src=&quot;https://evil&quot;&gt;inside&lt;/iframe&gt;"
    )
    boards.answers[employer.token] = payload
    crawl()
    description = postings_of(employer)["Staff Accountant"].description
    assert description == "Hello\n\nWrite <script> tags"
    assert "alert" not in description and "onclick" not in description and "evil" not in description


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<p>One</p><p>Two</p>", "One\n\nTwo"),
        ("<ul><li>A</li><li>B</li></ul>", "• A\n\n• B"),
        ("Line<br>break", "Line\nbreak"),
        ("Fish &amp; chips&nbsp;&mdash; daily", "Fish & chips — daily"),
        ("<style>.x{}</style><script>bad()</script>Kept", "Kept"),
        ("  lots   of\t\tspace  ", "lots of space"),
        ("", ""),
        (None, ""),
    ],
)
def test_plain_text(html, text):
    assert plain_text(html) == text


def test_a_description_is_capped():
    text = plain_text("<p>" + "word " * 20_000 + "</p>")
    assert len(text) == sources.DESCRIPTION_MAX and text.endswith("…")


def test_greenhouse_numeric_ids_and_offices_are_read():
    payload = {"jobs": [{"id": 8851677002, "title": "Analyst", "absolute_url": "https://x.test/j", "location": {"name": ""},
                         "offices": [{"name": "Pittsburgh, PA"}], "content": ""}]}  # fmt: skip
    [job] = greenhouse.parse(payload)
    assert job.external_id == "8851677002" and job.location == "Pittsburgh, PA"


def test_jobs_without_a_title_or_an_https_link_are_skipped():
    payload = {"jobs": [
        {"id": 1, "title": "", "absolute_url": "https://x.test/1"},
        {"id": 2, "title": "Ok", "absolute_url": "javascript:alert(1)"},
        {"id": 3, "title": "Ok", "absolute_url": "https://x.test/3"},
    ]}  # fmt: skip
    assert [job.external_id for job in greenhouse.parse(payload)] == ["3"]


def test_the_user_agent_says_who_we_are():
    assert sources.user_agent().startswith("JobHunt/") and "(+https://apply.seandesmet.com)" in sources.user_agent()


def test_record_is_idempotent_within_a_night(boards):
    """Two records at the same moment (a retried run) leave the same rows."""
    employer = boards.make("greenhouse")
    now = datetime.now(UTC) - timedelta(minutes=1)
    with SessionLocal() as db:
        fresh = db.get(Employer, employer.id)
        jobs = greenhouse.parse(fixture("greenhouse", employer.token))
        postings.record(db, fresh, jobs, now)
        postings.record(db, fresh, jobs, now)
        db.commit()
    assert len(postings_of(employer)) == 3
