"""Saved jobs (#63): paste a link, Job Hunt reads the posting.

Never the network: a page answers through `saved._open`, and DNS through
`saved._addresses`. Each test's links are its
own, and saved jobs belong to the test's own users, so tests share nothing.
"""

import json
import uuid

import pytest
from sqlalchemy import select

from app import saved
from app.db import SessionLocal
from app.models import SavedJob
from tests.conftest import login


class Web:
    """Stands in for the internet: `pages[url]` for pages ((status, headers, html)),
    and `dns[host]` for addresses (public by default)."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, dict, str]] = {}
        self.dns: dict[str, list[str]] = {}
        self.asked: list[str] = []

    def open(self, url: str):
        self.asked.append(url)
        status, headers, html = self.pages.get(url, (404, {}, ""))
        return status, {"content-type": "text/html; charset=utf-8", **headers}, html.encode()

    def addresses(self, host: str, port: int) -> list[str]:
        return self.dns.get(host, ["93.184.216.34"])


@pytest.fixture
def web(monkeypatch):
    fake = Web()
    monkeypatch.setattr(saved, "_open", fake.open)
    monkeypatch.setattr(saved, "_addresses", fake.addresses)
    return fake


@pytest.fixture
def signed_in(client, make_user):
    user = make_user()
    assert login(client, user.email).status_code == 303
    client.user = user
    return client


def csrf_of(client) -> str:
    from tests.conftest import csrf

    return csrf(client)


def paste(client, url: str):
    return client.post("/saved", data={"url": url, "csrf_token": csrf_of(client)}, follow_redirects=False)


def jobs_of(user) -> list[SavedJob]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(SavedJob).where(SavedJob.user_id == user.id).order_by(SavedJob.id)))
        db.expunge_all()
        return rows


def page_with(posting: dict) -> str:
    # "</" is written "<\/" inside the script, as real pages do, or "</script>" in a
    # description would end the block.
    data = json.dumps({"@context": "https://schema.org", "@graph": [{"@type": "Organization", "name": "x"}, posting]})
    return f"""<!doctype html><html><head><title>Careers</title>
<script type="application/ld+json">{data.replace("</", "<\\/")}</script>
</head><body><div id="app"></div></body></html>"""


POSTING = {
    "@type": "JobPosting",
    "title": "Financial Analyst",
    "hiringOrganization": {"@type": "Organization", "name": "Three Rivers Health"},
    "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Pittsburgh", "addressRegion": "PA", "addressCountry": "US"}}],  # fmt: skip
    "description": "<p>Build <b>budgets</b>.</p><script>alert(1)</script><ul><li>Excel</li></ul>",
}


# ---- From the page -----------------------------------------------------------------------


@pytest.mark.criterion(63, "A page with a JobPosting is read from it")
def test_a_page_with_a_jobposting_is_read_from_it(web, signed_in):
    link = f"https://careers.example.com/jobs/{uuid.uuid4().hex}"
    web.pages[link] = (200, {}, page_with(POSTING))
    paste(signed_in, link)
    [job] = jobs_of(signed_in.user)
    assert (job.source, job.title, job.company, job.location) == ("page", "Financial Analyst", "Three Rivers Health", "Pittsburgh, PA")  # fmt: skip
    assert job.description == "Build budgets.\n\n• Excel"
    assert "<" not in job.description and "alert" not in job.description


def test_a_remote_jobposting_and_an_escaped_description():
    posting = {**POSTING, "jobLocationType": "TELECOMMUTE", "jobLocation": None, "description": "&lt;p&gt;Hi&lt;/p&gt;", "hiringOrganization": "Acme"}  # fmt: skip
    read = saved.read_page(page_with(posting))
    assert read.remote and read.description == "Hi" and read.company == "Acme" and read.location == ""


def test_a_redirect_is_followed_and_checked(web, signed_in):
    first, second = f"https://example.com/j/{uuid.uuid4().hex}", f"https://careers.example.com/{uuid.uuid4().hex}"
    web.pages[first] = (302, {"location": second}, "")
    web.pages[second] = (200, {}, page_with(POSTING))
    paste(signed_in, first)
    assert jobs_of(signed_in.user)[0].title == "Financial Analyst"


@pytest.mark.criterion(63, "A page Job Hunt can't read asks for the text")
def test_a_page_without_a_posting_asks_for_the_text_and_takes_it(web, signed_in):
    link = f"https://careers.example.com/{uuid.uuid4().hex}"
    web.pages[link] = (200, {}, "<html><body><div id=app></div><script src=app.js></script></body></html>")
    paste(signed_in, link)
    [job] = jobs_of(signed_in.user)
    assert job.source == "pending" and job.read_error == saved.NOT_FOUND
    page = signed_in.get(f"/saved/{job.id}").text
    assert "Paste the posting" in page and "built by script" in page
    assert "Needs the text" in signed_in.get("/saved").text

    response = signed_in.post(
        f"/saved/{job.id}/text",
        data={"title": "Analyst <b>II</b>", "company": "Acme", "location": "Cranberry Township, PA", "text": "Do <script>x</script> things.", "csrf_token": csrf_of(signed_in)},  # fmt: skip
        follow_redirects=False,
    )
    assert response.status_code == 303
    [job] = jobs_of(signed_in.user)
    assert (job.source, job.title, job.company, job.location, job.read_error) == ("text", "Analyst <b>II</b>", "Acme", "Cranberry Township, PA", None)  # fmt: skip
    page = signed_in.get(f"/saved/{job.id}").text
    assert "Do &lt;script&gt;x&lt;/script&gt; things." in page and "<script>x" not in page
    assert "Paste the posting" not in page


def test_a_login_page_asks_for_the_text(web, signed_in):
    link = f"https://careers.example.com/{uuid.uuid4().hex}"
    web.pages[link] = (401, {}, "")
    paste(signed_in, link)
    assert jobs_of(signed_in.user)[0].read_error == saved.LOGIN


def test_the_text_needs_a_title_and_some_text(web, signed_in):
    link = f"https://careers.example.com/{uuid.uuid4().hex}"
    paste(signed_in, link)
    [job] = jobs_of(signed_in.user)
    response = signed_in.post(f"/saved/{job.id}/text", data={"title": "x", "text": "  ", "csrf_token": csrf_of(signed_in)})
    assert response.status_code == 422 and "Paste the posting&#39;s text." in response.text
    assert jobs_of(signed_in.user)[0].source == "pending"


# ---- LinkedIn, Indeed, challenges ----------------------------------------------------------


@pytest.mark.criterion(63, "LinkedIn and Indeed are never fetched")
@pytest.mark.parametrize("link", ["https://www.linkedin.com/jobs/view/123", "https://www.indeed.com/viewjob?jk=abc"])
def test_linkedin_and_indeed_are_never_fetched(web, signed_in, link):
    link += f"&t={uuid.uuid4().hex}" if "?" in link else f"?t={uuid.uuid4().hex}"
    paste(signed_in, link)
    [job] = jobs_of(signed_in.user)
    assert job.source == "pending" and job.read_error == saved.BANNED
    assert web.asked == []


def test_a_redirect_to_linkedin_is_not_followed(web, signed_in):
    link = f"https://example.com/{uuid.uuid4().hex}"
    web.pages[link] = (301, {"location": "https://www.linkedin.com/jobs/view/9"}, "")
    paste(signed_in, link)
    assert jobs_of(signed_in.user)[0].read_error == saved.BANNED
    assert web.asked == [link]


@pytest.mark.criterion(63, "A challenge is never got around")
@pytest.mark.parametrize(("status", "headers"), [(403, {"cf-mitigated": "challenge"}), (429, {}), (503, {}), (200, {"cf-mitigated": "challenge"})])  # fmt: skip
def test_a_challenge_stops_at_one_request(web, signed_in, status, headers):
    link = f"https://careers.example.com/{uuid.uuid4().hex}"
    web.pages[link] = (status, headers, "<html>Just a moment...</html>")
    paste(signed_in, link)
    [job] = jobs_of(signed_in.user)
    assert job.read_error == saved.REFUSED and web.asked == [link]


# ---- Only the public internet ----------------------------------------------------------------


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "172.18.0.2", "192.168.1.1", "169.254.169.254", "::1", "fd00::1", "0.0.0.0"])  # fmt: skip
def test_a_host_on_a_private_address_is_never_requested(web, signed_in, address):
    host = f"{uuid.uuid4().hex[:8]}.example.com"
    web.dns[host] = ["93.184.216.34", address]  # any private address is enough to refuse
    paste(signed_in, f"https://{host}/job")
    [job] = jobs_of(signed_in.user)
    assert job.read_error == "Job Hunt only opens pages on the public internet." and web.asked == []


def test_a_redirect_to_a_private_address_is_not_followed(web, signed_in):
    link = f"https://example.com/{uuid.uuid4().hex}"
    web.pages[link] = (302, {"location": "http://db/"}, "")
    web.dns["db"] = ["172.18.0.3"]
    paste(signed_in, link)
    assert jobs_of(signed_in.user)[0].read_error == "Job Hunt only opens pages on the public internet."
    assert web.asked == [link]


@pytest.mark.parametrize("link", ["https://example.com:8443/job", "http://example.com:5432/"])
def test_only_the_usual_ports(web, link):
    with pytest.raises(saved.Unreadable, match="usual ports"):
        saved.check_destination(link)


@pytest.mark.parametrize(
    ("link", "message"),
    [("ftp://example.com/x", "look like a link"), ("not a link", "look like a link"), ("https://me:pw@example.com/x", "without a username")],  # fmt: skip
)
def test_a_bad_link_is_refused_and_nothing_is_saved(web, signed_in, link, message):
    response = paste(signed_in, link)
    assert response.status_code == 422 and message in response.text
    assert jobs_of(signed_in.user) == [] and web.asked == []


def test_too_many_redirects(web, signed_in):
    base = f"https://example.com/{uuid.uuid4().hex}"
    for n in range(saved.MAX_REDIRECTS + 1):
        web.pages[f"{base}/{n}"] = (302, {"location": f"{base}/{n + 1}"}, "")
    paste(signed_in, f"{base}/0")
    assert "redirected too many times" in jobs_of(signed_in.user)[0].read_error


# ---- Private, and once -----------------------------------------------------------------------


@pytest.mark.criterion(63, "A pasted job is mine alone")
def test_a_pasted_job_is_mine_alone(web, client, make_user):
    owner, other = make_user(), make_user()
    link = f"https://careers.example.com/{uuid.uuid4().hex}"
    web.pages[link] = (200, {}, page_with(POSTING))
    assert login(client, owner.email).status_code == 303
    paste(client, link)
    [job] = jobs_of(owner)

    client.cookies.clear()
    assert login(client, other.email).status_code == 303
    assert "Financial Analyst" not in client.get("/saved").text
    assert client.get(f"/saved/{job.id}").status_code == 404
    token = csrf_of(client)
    assert client.post(f"/saved/{job.id}/text", data={"title": "x", "text": "y", "csrf_token": token}).status_code == 404
    assert client.post(f"/saved/{job.id}/delete", data={"csrf_token": token}).status_code == 404
    assert len(jobs_of(owner)) == 1 and jobs_of(other) == []


def test_saved_jobs_need_a_login(client):
    response = client.get("/saved", follow_redirects=False)
    assert response.status_code in (302, 303, 401) or "/login" in response.headers.get("location", "")


def test_the_same_link_twice_is_one_job(web, signed_in):
    link = f"https://careers.example.com/{uuid.uuid4().hex}"
    web.pages[link] = (200, {}, page_with(POSTING))
    paste(signed_in, link)
    response = paste(signed_in, f"  {link}  ")
    assert len(jobs_of(signed_in.user)) == 1 and web.asked == [link]
    assert response.headers["location"] == f"/saved/{jobs_of(signed_in.user)[0].id}"


def test_a_link_still_waiting_is_read_again_when_pasted_again(web, signed_in):
    link = f"https://careers.example.com/{uuid.uuid4().hex}"
    paste(signed_in, link)
    web.pages[link] = (200, {}, page_with(POSTING))
    paste(signed_in, link)
    assert jobs_of(signed_in.user)[0].source == "page"


def test_a_saved_job_can_be_removed(web, signed_in):
    link = f"https://careers.example.com/{uuid.uuid4().hex}"
    web.pages[link] = (200, {}, page_with(POSTING))
    paste(signed_in, link)
    [job] = jobs_of(signed_in.user)
    response = signed_in.post(f"/saved/{job.id}/delete", data={"csrf_token": csrf_of(signed_in)}, follow_redirects=False)
    assert response.status_code == 303 and jobs_of(signed_in.user) == []


MICRODATA = """<html><body>
<main itemscope itemtype="http://schema.org/JobPosting">
  <meta itemprop="datePosted" content="2026-11-18">
  <h1 class="job-title" itemprop="title">Safety  Engineer</h1>
  <div itemprop="hiringOrganization" itemscope itemtype="http://schema.org/Organization">
    <meta itemprop="name" content="Allegheny Robotics">
  </div>
  <ul><li itemprop="jobLocation" itemscope itemtype="http://schema.org/Place">
    <div itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
      <span itemprop="addressLocality">Pittsburgh</span>, <span itemprop="addressRegion">PA</span>
      <meta itemprop="addressCountry" content="US">
    </div>
  </li></ul>
  <section itemprop="description"><h3>Company Description</h3><p>We build <b>robots</b> &amp; more.</p><script>track()</script></section>
  <section itemprop="responsibilities"><ul><li>Audit sites</li><li>Write reports<br>weekly</li></ul></section>
  <p>Unclosed paragraph
</main>
</body></html>"""


def test_a_page_with_microdata_is_read_from_it():
    read = saved.read_page(MICRODATA)
    assert (read.title, read.company, read.location) == ("Safety Engineer", "Allegheny Robotics", "Pittsburgh, PA")
    assert read.description == "Company Description\n\nWe build robots & more.\n\nAudit sites\n\nWrite reports\nweekly"
    assert "track()" not in read.description


def test_json_ld_wins_over_microdata():
    html = page_with(POSTING).replace("</body>", MICRODATA + "</body>")
    assert saved.read_page(html).title == "Financial Analyst"
