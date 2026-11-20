"""Saved jobs (#63): the user pastes a link to a job they found, and Job Hunt reads it.

Discovery by the user (plan §4, §10 decision 8). The posting is read, in this order:

1. **A Greenhouse, Lever or Ashby job link** is read from that system's public API,
   for that one job (no page is loaded).
2. **Any other page** gets one GET, identified as Job Hunt, and its schema.org
   `JobPosting` (JSON-LD, which most career pages embed for search engines) is read.
3. **Otherwise** the job is saved as `pending` with the reason, and the user pastes
   the posting's text.

Never fetched: LinkedIn or Indeed (non-negotiable 9). A bot challenge, a login wall
or a refusal stops at that one request; it's never retried or got around.

The GET to an arbitrary page is guarded, because the server makes it for a URL a
person typed (SSRF): http(s) on the standard ports only, every address the host
resolves to must be public, each redirect is checked the same way (at most
MAX_REDIRECTS), and the body is capped. What's read is stored as plain text.

⚠️ Private data: every function takes the current user and touches only their rows
(non-negotiable 1); someone else's job id is a 404.
"""

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx2
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import area
from app.models import Employer, SavedJob, User
from app.sources import (
    _BLOCK,
    _DROP,
    DESCRIPTION_MAX,
    FetchError,
    ashby,
    banned_link,
    clip,
    get_json,
    greenhouse,
    lever,
    plain_text,
    tidy,
    user_agent,
)

URL_MAX = 1000
PAGE_MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3
PORTS = {"http": 80, "https": 443}

BANNED = "Job Hunt never opens LinkedIn or Indeed pages. Paste the posting's text instead."
REFUSED = "That site refused Job Hunt (a bot check), and Job Hunt doesn't get around those. Paste the posting's text instead."  # fmt: skip
LOGIN = "That page needs a login. Paste the posting's text instead."
NOT_FOUND = "Job Hunt couldn't find a job posting on that page (it may be built by script). Paste the posting's text instead."  # fmt: skip


class LinkError(Exception):
    """A link that can't be saved at all, worded for the user."""


class Unreadable(Exception):
    """Why a saved link couldn't be read, worded for the user; it waits for text."""


@dataclass(frozen=True)
class Read:
    source: str
    title: str
    company: str
    location: str
    description: str
    remote: bool = False


# ---- The link --------------------------------------------------------------------------


def clean_url(url: str) -> str:
    """The pasted link, checked for shape: http(s), a host, no login in it."""
    url = (url or "").strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in PORTS or not parts.hostname or "." not in parts.hostname:
        raise LinkError("That doesn't look like a link to a web page.")
    if parts.username or parts.password:
        raise LinkError("Paste the link without a username or password in it.")
    if len(url) > URL_MAX:
        raise LinkError(f"That link is longer than {URL_MAX} characters.")
    return url


_BOARD_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def board_job(url: str) -> tuple[str, str, str] | None:
    """(system, board token, job id) for a link to one job on Greenhouse
    (…/<token>/jobs/<number>), Lever (jobs.lever.co/<token>/<uuid>) or Ashby
    (jobs.ashbyhq.com/<token>/<uuid>); a trailing /apply or /application is fine.
    None for anything else."""
    parts = urlsplit(url)
    system = _BOARD_HOSTS.get((parts.hostname or "").lower())
    segments = [s for s in parts.path.split("/") if s]
    if system is None or len(segments) < 2 or not _SLUG.match(segments[0]):
        return None
    if system == "greenhouse":
        if len(segments) >= 3 and segments[1] == "jobs" and segments[2].isdigit():
            return system, segments[0], segments[2]
        return None
    return (system, segments[0], segments[1].lower()) if _UUID.match(segments[1]) else None


# ---- Reading from a hiring system's API --------------------------------------------------


def _company(db: Session, system: str, token: str, given: object = None) -> str:
    """The registry's name for the board, else the system's own, else the token."""
    known = db.scalar(select(Employer.name).where(Employer.system == system, Employer.token == token))
    if known:
        return known
    if isinstance(given, str) and given.strip():
        return tidy(given, 200)
    return token.replace("-", " ").replace("_", " ").title()[:200]


def read_board(db: Session, system: str, token: str, job_id: str) -> Read:
    """One job from its hiring system's API. Unreadable if it isn't there."""
    try:
        if system == "greenhouse":
            payload = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}")
            company = payload.get("company_name") if isinstance(payload, dict) else None
            found = greenhouse.parse({"jobs": [payload]})
        elif system == "lever":
            company = None
            found = lever.parse([get_json(f"https://api.lever.co/v0/postings/{token}/{job_id}?mode=json")])
        else:
            company = None  # Ashby has no one-job endpoint: the board, then this job
            found = [job for job in ashby.parse(get_json(ashby.url(token))) if job.external_id.lower() == job_id]
    except FetchError as error:
        label = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}[system]
        if "404" in str(error):
            raise Unreadable(f"{label} has no open job at that link. It may have closed.") from None
        raise Unreadable(f"{label} {error}. Try again later, or paste the posting's text.") from None
    if not found:
        raise Unreadable("That job isn't listed any more. It may have closed.")
    job = found[0]
    return Read(system, job.title, _company(db, system, token, company), job.location, job.description, job.remote)


# ---- Reading any other page ------------------------------------------------------------


def _addresses(host: str, port: int) -> list[str]:
    """Every address the host resolves to (tests replace this)."""
    try:
        return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    except (socket.gaierror, UnicodeError):
        raise Unreadable("That site's address couldn't be found. Check the link.") from None


def check_destination(url: str) -> None:
    """Refuse a URL the server mustn't request: another scheme or port, or a host
    that resolves to anything but public addresses (the droplet, its network, the
    database). Unreadable, worded for the user."""
    parts = urlsplit(url)
    if parts.scheme not in PORTS or not parts.hostname or parts.username or parts.password:
        raise Unreadable("That link doesn't lead to a web page Job Hunt can open.")
    port = parts.port or PORTS[parts.scheme]
    if port != PORTS[parts.scheme]:
        raise Unreadable("Job Hunt only opens web pages on the usual ports.")
    addresses = _addresses(parts.hostname, port)
    if not addresses or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses):
        raise Unreadable("Job Hunt only opens pages on the public internet.")


def _open(url: str) -> tuple[int, dict[str, str], bytes]:
    """One GET, no redirects followed (tests replace this): (status, headers, body),
    the body capped at PAGE_MAX_BYTES."""
    headers = {"User-Agent": user_agent(), "Accept": "text/html,application/xhtml+xml"}
    try:
        with httpx2.stream("GET", url, headers=headers, timeout=TIMEOUT_SECONDS, follow_redirects=False) as response:
            body = bytearray()
            if response.status_code == 200:
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > PAGE_MAX_BYTES:
                        raise Unreadable("That page is too big for Job Hunt to read. Paste the posting's text instead.")
            return response.status_code, {k.lower(): v for k, v in response.headers.items()}, bytes(body)
    except httpx2.TimeoutException:
        raise Unreadable(f"That site didn't answer within {TIMEOUT_SECONDS} seconds. Try again, or paste the text.") from None
    except httpx2.HTTPError:
        raise Unreadable("That site couldn't be reached. Check the link, or paste the posting's text.") from None


def fetch_page(url: str) -> str:
    """The page's HTML, after checking every hop. Unreadable on a challenge, a login,
    a refusal or anything but a page."""
    for _ in range(MAX_REDIRECTS + 1):
        if banned_link(url):
            raise Unreadable(BANNED)
        check_destination(url)
        status, headers, body = _open(url)
        if status in (301, 302, 303, 307, 308) and headers.get("location"):
            url = urljoin(url, headers["location"])
            continue
        if "cf-mitigated" in headers or status in (403, 429, 503):
            raise Unreadable(REFUSED)
        if status == 401:
            raise Unreadable(LOGIN)
        if status != 200:
            raise Unreadable(f"That page answered {status}. Paste the posting's text instead.")
        if "html" not in headers.get("content-type", "text/html"):
            raise Unreadable("That link isn't a web page. Paste the posting's text instead.")
        return body.decode("utf-8", errors="replace")
    raise Unreadable("That link redirected too many times. Paste the posting's text instead.")


_LD_JSON = re.compile(r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)


def _postings(node: object):
    """Every JobPosting in a JSON-LD tree (a list, an @graph, or nested)."""
    if isinstance(node, list):
        for item in node:
            yield from _postings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node
        elif "@graph" in node:
            yield from _postings(node["@graph"])


def _name(value: object) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = _first_text(value)
    return " ".join(value.split()) if isinstance(value, str) else ""


def _place(place: object) -> str:
    if not isinstance(place, dict):
        return ""
    address = place.get("address")
    if isinstance(address, str):
        return address.strip()
    if not isinstance(address, dict):
        return _name(place)
    town, region, country = (_name(address.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry"))
    if country.upper() in ("US", "USA", "UNITED STATES"):
        country = ""
    return ", ".join(p for p in (town, region, country) if p)


class _Microdata(HTMLParser):
    """schema.org microdata (itemscope / itemtype / itemprop), as SmartRecruiters and
    others mark up a job page: each JobPosting as the same nested dicts JSON-LD gives.
    A property's value is its `content` (a <meta>), else its text, with a line break
    for each block element so a description keeps its paragraphs."""

    _VOID = {"meta", "link", "br", "img", "hr", "input", "source", "area", "base", "col", "embed", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict | None, str | None, list[str] | None, dict | None]] = []
        self.scopes: list[dict] = []  # the open itemscopes, innermost last
        self.found: list[dict] = []
        self.dropping = 0

    def _put(self, scope: dict | None, prop: str, value: object) -> None:
        if scope is None:
            return
        for name in prop.split():
            if name in scope:
                scope[name] = (scope[name] if isinstance(scope[name], list) else [scope[name]]) + [value]
            else:
                scope[name] = value

    def handle_starttag(self, tag, attrs):
        attrs = {k: v or "" for k, v in attrs}
        if tag in _DROP:
            self.dropping += 1
        outer = self.scopes[-1] if self.scopes else None
        prop = attrs.get("itemprop", "").strip() or None
        if tag in self._VOID or tag == "meta":
            if prop and "content" in attrs:
                self._put(outer, prop, attrs["content"].strip())
            if tag in _BLOCK:
                self._text("\n")
            return
        scope = None
        if "itemscope" in attrs:
            kind = attrs.get("itemtype", "").rstrip("/").rsplit("/", 1)[-1]
            scope = {"@type": kind}
            self.scopes.append(scope)
        capture = [] if prop and scope is None and "content" not in attrs else None
        if prop and scope is None and "content" in attrs:
            self._put(outer, prop, attrs["content"].strip())
        self.stack.append((tag, scope, prop, capture, outer))
        if tag in _BLOCK:
            self._text("\n")

    def handle_endtag(self, tag):
        if tag in _DROP:
            self.dropping = max(0, self.dropping - 1)
        if tag in _BLOCK:
            self._text("\n")
        if not any(entry[0] == tag for entry in self.stack):
            return  # a stray end tag
        while self.stack:
            name, scope, prop, capture, outer = self.stack.pop()
            if scope is not None:
                self.scopes.remove(scope)
                if prop:
                    self._put(outer, prop, scope)
                elif scope.get("@type") == "JobPosting":
                    self.found.append(scope)
            elif capture is not None:
                self._put(outer, prop, "".join(capture))
            if name == tag:
                break

    def handle_data(self, data):
        if not self.dropping:
            self._text(data)

    def _text(self, data: str) -> None:
        for entry in self.stack:
            if entry[3] is not None:
                entry[3].append(data)


def _microdata(html: str) -> list[dict]:
    parser = _Microdata()
    parser.feed(html)
    parser.close()
    return parser.found


def _first_text(value: object) -> str:
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str) and v.strip()), "")
    return value if isinstance(value, str) else ""


def _from_posting(posting: dict, *, markup: bool) -> Read | None:
    """A Read from one JobPosting (JSON-LD's or microdata's), or None without a title.
    `markup`: the description is HTML (JSON-LD's); microdata's is already text."""
    title = tidy(_first_text(posting.get("title")), 300)
    if not title:
        return None
    places = posting.get("jobLocation")
    places = places if isinstance(places, list) else [places]
    location = "; ".join(dict.fromkeys(p for p in (_place(x) for x in places) if p))
    kind = posting.get("jobLocationType")
    remote = "TELECOMMUTE" in (kind if isinstance(kind, list) else [kind])
    if markup:
        description = _first_text(posting.get("description"))
        text = plain_text(description, escaped="&lt;" in description and "<" not in description)
    else:
        parts = (_first_text(posting.get(k)) for k in ("description", "responsibilities", "qualifications"))
        text = clip(plain_text_lines("\n\n".join(part for part in parts if part.strip())), DESCRIPTION_MAX)
    return Read("page", title, tidy(_name(posting.get("hiringOrganization")), 200), tidy(location, 500), text, remote)


_SPACES = re.compile(r"[ \t\r\f\v\u00a0]+")
_BLANKS = re.compile(r"\n\s*\n\s*\n+")


def plain_text_lines(text: str) -> str:
    """Text already free of markup, tidied the way plain_text() tidies."""
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def read_page(html: str) -> Read:
    """The first JobPosting with a title on the page: JSON-LD first, then microdata.
    Unreadable if there's neither."""
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block.strip(), strict=False)
        except ValueError:
            continue
        for posting in _postings(data):
            if (found := _from_posting(posting, markup=True)) is not None:
                return found
    for posting in _microdata(html):
        if (found := _from_posting(posting, markup=False)) is not None:
            return found
    raise Unreadable(NOT_FOUND)


# ---- Saving ----------------------------------------------------------------------------


def for_user(db: Session, user: User) -> list[SavedJob]:
    return list(db.scalars(select(SavedJob).where(SavedJob.user_id == user.id).order_by(SavedJob.created_at.desc(), SavedJob.id.desc())))  # fmt: skip


def get(db: Session, user: User, job_id: int) -> SavedJob:
    """The user's own saved job, or 404 (someone else's is no different from none)."""
    job = db.scalar(select(SavedJob).where(SavedJob.id == job_id, SavedJob.user_id == user.id))
    if job is None:
        raise HTTPException(status_code=404)
    return job


def _apply(job: SavedJob, read: Read) -> None:
    job.source = read.source
    job.read_error = None
    job.title, job.company = read.title, read.company or job.company
    job.location, job.remote = read.location, read.remote
    where = area.classify(read.location)
    job.area = area.Where.REMOTE.value if read.remote and where is not area.Where.IN_AREA else where.value
    job.description = clip(read.description, DESCRIPTION_MAX)


def read(db: Session, url: str) -> Read:
    """Read the posting a link leads to: its hiring system's API, else the page's
    JobPosting. Unreadable, with the reason, when neither works."""
    if banned_link(url):
        raise Unreadable(BANNED)
    if (board := board_job(url)) is not None:
        return read_board(db, *board)
    return read_page(fetch_page(url))


def paste(db: Session, user: User, url: str) -> tuple[SavedJob, bool]:
    """Save the user's link and read it (uncommitted): (the job, whether it was new).
    A link already saved is that job, not a second one; if it's still waiting for
    text it's read again. LinkError if the link itself is no good."""
    url = clean_url(url)
    job = db.scalar(select(SavedJob).where(SavedJob.user_id == user.id, SavedJob.url == url))
    new = job is None
    if job is None:
        job = SavedJob(user_id=user.id, url=url)
        db.add(job)
    elif job.source != "pending":
        return job, False
    try:
        _apply(job, read(db, url))
    except Unreadable as reason:
        job.source, job.read_error = "pending", str(reason)[:300]
    db.flush()
    return job, new


def add_text(db: Session, job: SavedJob, *, title: str, company: str, location: str, text: str) -> None:
    """The user's own copy of the posting (uncommitted). LinkError if it's not enough."""
    title, text = tidy(title, 300), (text or "").strip()
    if not title:
        raise LinkError("Give the job a title.")
    if not text:
        raise LinkError("Paste the posting's text.")
    if len(text) > DESCRIPTION_MAX:
        raise LinkError(f"That's longer than {DESCRIPTION_MAX:,} characters. Paste just the posting.")
    _apply(job, Read("text", title, tidy(company, 200), tidy(location, 500), text, False))
    db.flush()
