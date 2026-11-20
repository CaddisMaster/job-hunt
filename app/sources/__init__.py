"""Where postings come from (#44, plan §4): each hiring system's public job-board API.

One module per system turns that API's answer into `Found` jobs; `fetch(employer)`
makes the one request a night an employer gets and hands back its open jobs, or
raises `FetchError` with a short reason for the admin. No login, no page scraping:
only the three API hosts below are ever requested, with a board token the registry
already checked (app/employers.py).

Polite by construction: one GET per employer, a 20 s timeout per network step and a
total deadline, a User-Agent that says who we are, no retries (a failed board is
tried again the next night), and a size cap so one huge board can't sink the worker.
"""

import json
import re
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

import httpx2

from app.config import get_settings

TIMEOUT_SECONDS = 20
DEADLINE_SECONDS = 90  # the whole response, however slowly it trickles in
MAX_BYTES = 25 * 1024 * 1024  # the biggest real board seen (2026-11-19) was about 6 MB
DESCRIPTION_MAX = 50_000  # the postings table's check constraint
APP_URL = "https://apply.seandesmet.com"


class FetchError(Exception):
    """Why an employer's board couldn't be fetched, worded for the admin page."""


@dataclass(frozen=True)
class Found:
    """One open job, as a source described it, before it's a posting."""

    external_id: str
    title: str
    url: str
    location: str
    description: str  # plain text already (plain_text)
    remote: bool = False
    latitude: float | None = None
    longitude: float | None = None


def user_agent() -> str:
    return f"JobHunt/{get_settings().app_version} (+{APP_URL})"


def _get(url: str) -> bytes:
    """The live request (tests replace this). FetchError on anything but a 200."""
    started = time.monotonic()
    headers = {"Accept": "application/json", "User-Agent": user_agent()}
    try:
        with httpx2.stream("GET", url, headers=headers, timeout=TIMEOUT_SECONDS, follow_redirects=False) as response:
            if response.status_code != 200:
                raise FetchError(f"answered {response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise FetchError(f"sent more than {MAX_BYTES // (1024 * 1024)} MB")
                if time.monotonic() - started > DEADLINE_SECONDS:
                    raise FetchError(f"took longer than {DEADLINE_SECONDS} s")
            return bytes(body)
    except httpx2.TimeoutException:
        raise FetchError(f"didn't answer within {TIMEOUT_SECONDS} s") from None
    except httpx2.HTTPError as error:
        raise FetchError(f"couldn't be reached ({type(error).__name__})") from None


def get_json(url: str) -> object:
    try:
        return json.loads(_get(url))
    except (ValueError, UnicodeDecodeError):
        raise FetchError("didn't answer with JSON") from None


# ---- Descriptions: markup in, plain text out -------------------------------------

_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
    "section", "article", "header", "footer", "blockquote", "pre", "hr", "dd", "dt",
}  # fmt: skip
_DROP = {"script", "style", "noscript", "template", "iframe", "object", "svg", "head"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.dropping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP:
            self.dropping += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        if tag == "li" and not self.dropping:
            self.parts.append("• ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _DROP:
            self.dropping = max(0, self.dropping - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.dropping:
            self.parts.append(data)


_SPACES = re.compile(r"[ \t\r\f\v ]+")
_BLANK_LINES = re.compile(r"\n\s*\n\s*\n+")


def plain_text(html: str | None, *, escaped: bool = False) -> str:
    """A description as plain text: tags removed (and the contents of script, style
    and the like), entities decoded, whitespace tidied, capped at DESCRIPTION_MAX.

    `escaped` is for Greenhouse, which sends its HTML entity-encoded
    ("&lt;p&gt;…"). What comes out is text to show escaped, never markup: a
    "<script>" that survives as words stays words."""
    if not html:
        return ""
    if escaped:
        html = unescape(html)
    parser = _Text()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return clip(text, DESCRIPTION_MAX)


def clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def tidy(text: object, limit: int) -> str:
    """A short field (title, location) as one clean line: only strings count."""
    if not isinstance(text, str):
        return ""
    return clip(_SPACES.sub(" ", unescape(text)).replace("\n", " "), limit)
