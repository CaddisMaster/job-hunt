"""The shared employer registry (#43): employers whose own hiring system has a public
job-board API (plan §4, the most reliable source).

The admin adds one by pasting its careers-page URL. The hiring system and board token
are read from the URL, never guessed, and one live request to that system's API
checks the board exists before anything is saved. Only these three systems' API hosts
are ever requested, with a token that matched TOKEN_RE, so a pasted URL can't send the
server anywhere else.

Shared by every user: no `user_id`. The admin page shows employers only, never whose
profile or search led to one (plan §2).
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employer, User

SYSTEMS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}
SUPPORTED = "Greenhouse (boards.greenhouse.io/…), Lever (jobs.lever.co/…) or Ashby (jobs.ashbyhq.com/…)"

# Board tokens are slugs. Ashby's may hold a dot ("acme.inc"), the others don't.
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

# The one request that checks a board: its public API, by system.
_CHECK_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json&limit=1",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}
_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}
TIMEOUT_SECONDS = 10


class RegistryError(Exception):
    """Why an employer couldn't be added, worded for the admin."""


@dataclass(frozen=True)
class Board:
    system: str
    token: str


def parse_careers_url(url: str) -> Board:
    """The hiring system and board token a careers URL names, or RegistryError.

    boards.greenhouse.io/<token>, job-boards.greenhouse.io/<token> and Greenhouse's
    embed form (…/embed/job_board?for=<token>); jobs.lever.co/<token>;
    jobs.ashbyhq.com/<token>. Anything after the token (a job's id) is ignored."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    system = _HOSTS.get(host)
    if parts.scheme not in ("http", "https") or system is None:
        raise RegistryError(f"That isn't a careers page Job Hunt can read. It supports {SUPPORTED}.")
    segments = [segment for segment in parts.path.split("/") if segment]
    if system == "greenhouse" and segments[:2] == ["embed", "job_board"]:
        token = (parse_qs(parts.query).get("for") or [""])[0]
    else:
        token = segments[0] if segments else ""
    if not TOKEN_RE.match(token):
        raise RegistryError(f"That {SYSTEMS[system]} URL doesn't name a job board. Paste the employer's careers page.")
    return Board(system, token)


def _get(url: str) -> httpx2.Response:
    """The live request (tests replace this)."""
    return httpx2.get(url, timeout=TIMEOUT_SECONDS, follow_redirects=False, headers={"Accept": "application/json"})


def check_board(board: Board) -> str | None:
    """Make one request to the board's API: the employer's name if the system says
    it (Greenhouse does), else None. RegistryError if there's no such board, or the
    system couldn't be asked."""
    label = SYSTEMS[board.system]
    try:
        response = _get(_CHECK_URLS[board.system].format(token=board.token))
    except httpx2.HTTPError:
        raise RegistryError(f"Couldn't reach {label} to check that board. Nothing was saved; try again.") from None
    if response.status_code == 404:
        raise RegistryError(f"{label} has no job board called “{board.token}”. Check the URL.")
    if response.status_code != 200:
        raise RegistryError(f"{label} answered {response.status_code} when checking that board. Nothing was saved; try again later.")
    if board.system == "greenhouse":
        try:
            name = response.json().get("name")
        except (ValueError, AttributeError):
            name = None
        return name.strip()[:200] if isinstance(name, str) and name.strip() else None
    return None


def add(db: Session, careers_url: str, name: str, admin: User) -> Employer:
    """Parse, refuse a duplicate, check live, then add (uncommitted). The name typed
    wins; blank takes the hiring system's own name, if it gave one."""
    board = parse_careers_url(careers_url)
    existing = db.scalar(select(Employer).where(Employer.system == board.system, Employer.token == board.token))
    if existing is not None:
        raise RegistryError(f"{existing.name} is already in the registry ({SYSTEMS[board.system]}, “{board.token}”).")
    found = check_board(board)
    chosen = name.strip() or found
    if not chosen:
        raise RegistryError(f"{SYSTEMS[board.system]} doesn't say the employer's name. Type it in and add it again.")
    employer = Employer(
        name=chosen[:200],
        system=board.system,
        token=board.token,
        careers_url=careers_url.strip()[:500],
        added_by=admin.id,
    )
    db.add(employer)
    return employer


def all_employers(db: Session) -> list[Employer]:
    return list(db.scalars(select(Employer).order_by(Employer.name, Employer.id)))


def to_fetch(db: Session) -> list[Employer]:
    """What the nightly crawl (#44) fetches: enabled employers only."""
    return list(db.scalars(select(Employer).where(Employer.enabled).order_by(Employer.id)))


# ---- The starter list -------------------------------------------------------------

STARTERS = Path(__file__).parent / "data" / "employers.csv"
_CAREERS_URLS = {
    "greenhouse": "https://boards.greenhouse.io/{token}",
    "lever": "https://jobs.lever.co/{token}",
    "ashby": "https://jobs.ashbyhq.com/{token}",
}


def starters() -> list[tuple[str, Board]]:
    """The Pittsburgh starter list (app/data/employers.csv): employers whose board had
    jobs in the area when the list was built. Each row is checked the way a pasted URL
    is, so a bad row can't name another host."""
    rows = []
    with STARTERS.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["system"] in SYSTEMS and TOKEN_RE.match(row["token"]) and row["name"].strip():
                rows.append((row["name"].strip()[:200], Board(row["system"], row["token"])))
    return rows


def add_starters(db: Session, admin: User) -> tuple[int, int]:
    """Add every starter-list employer the registry doesn't have (uncommitted):
    (added, already there). No live check per board: the list was checked when it was
    built, and the crawl records any board that has since gone on its employer.
    An employer the admin disabled stays disabled."""
    have = {(system, token) for system, token in db.execute(select(Employer.system, Employer.token))}
    added = 0
    for name, board in starters():
        if (board.system, board.token) in have:
            continue
        have.add((board.system, board.token))
        db.add(
            Employer(
                name=name,
                system=board.system,
                token=board.token,
                careers_url=_CAREERS_URLS[board.system].format(token=board.token),
                added_by=admin.id,
            )
        )
        added += 1
    return added, len(starters()) - added
