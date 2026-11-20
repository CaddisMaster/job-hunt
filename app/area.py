"""Is a job in Greater Pittsburgh? (#42, plan §4)

The area is the seven-county metro (Allegheny, Armstrong, Beaver, Butler, Fayette,
Washington, Westmoreland) plus anywhere within 50 miles of downtown. Every source
words locations differently, so `classify()` reads the posting's own text, offline
and without a model, against app/data/places.csv (built from the Census Gazetteer
by scripts/build_places.py), and checks coordinates when a source gives them.

    in_area      a place in the table, a county, or coordinates within the radius
    remote       says it's remote and names no place in the area
    out_of_area  names a place, but not one in the area (Pittsburg, KS; New York, NY)
    unknown      can't tell: kept out, and counted for the admin (decided 2026-11-19)

A posting listing several locations is in the area if any one is. A hybrid or
on-site job in Pittsburgh is in the area, not remote. A bare town name with no
state ("Washington") is only trusted for Pittsburgh itself: too many towns share
names with places elsewhere, and a wrong "yes" shows someone a job they can't take.
"""

import csv
import math
import re
from enum import StrEnum
from functools import cache
from pathlib import Path

PLACES = Path(__file__).parent / "data" / "places.csv"
DOWNTOWN = (40.4406, -79.9959)
RADIUS_MILES = 50
COUNTIES = ("allegheny", "armstrong", "beaver", "butler", "fayette", "washington", "westmoreland")

STATES = {
    "al": "AL", "alabama": "AL", "ak": "AK", "alaska": "AK", "az": "AZ", "arizona": "AZ", "ar": "AR", "arkansas": "AR",
    "ca": "CA", "california": "CA", "co": "CO", "colorado": "CO", "ct": "CT", "connecticut": "CT", "de": "DE",
    "delaware": "DE", "dc": "DC", "district of columbia": "DC", "fl": "FL", "florida": "FL", "ga": "GA",
    "georgia": "GA", "hi": "HI", "hawaii": "HI", "id": "ID", "idaho": "ID", "il": "IL", "illinois": "IL", "in": "IN",
    "indiana": "IN", "ia": "IA", "iowa": "IA", "ks": "KS", "kansas": "KS", "ky": "KY", "kentucky": "KY", "la": "LA",
    "louisiana": "LA", "me": "ME", "maine": "ME", "md": "MD", "maryland": "MD", "ma": "MA", "massachusetts": "MA",
    "mi": "MI", "michigan": "MI", "mn": "MN", "minnesota": "MN", "ms": "MS", "mississippi": "MS", "mo": "MO",
    "missouri": "MO", "mt": "MT", "montana": "MT", "ne": "NE", "nebraska": "NE", "nv": "NV", "nevada": "NV",
    "nh": "NH", "new hampshire": "NH", "nj": "NJ", "new jersey": "NJ", "nm": "NM", "new mexico": "NM", "ny": "NY",
    "new york": "NY", "nc": "NC", "north carolina": "NC", "nd": "ND", "north dakota": "ND", "oh": "OH", "ohio": "OH",
    "ok": "OK", "oklahoma": "OK", "or": "OR", "oregon": "OR", "pa": "PA", "penn": "PA", "pennsylvania": "PA",
    "ri": "RI", "rhode island": "RI", "sc": "SC", "south carolina": "SC", "sd": "SD", "south dakota": "SD",
    "tn": "TN", "tennessee": "TN", "tx": "TX", "texas": "TX", "ut": "UT", "utah": "UT", "vt": "VT", "vermont": "VT",
    "va": "VA", "virginia": "VA", "wa": "WA", "washington": "WA", "washington state": "WA", "wv": "WV", "west virginia": "WV",
    "wi": "WI", "wisconsin": "WI", "wy": "WY", "wyoming": "WY",
}
# Anything after these words is a country, which tells us nothing more.
_COUNTRY = re.compile(r",?\s*\b(united states( of america)?|usa|u\.s\.a?\.?|us)\b\.?$")
_ZIP = re.compile(r"\b\d{5}(-\d{4})?\b")
_REMOTE = re.compile(r"\b(remote|work from home|wfh|telecommute|anywhere|distributed|virtual)\b")
_HYBRID = re.compile(r"\b(hybrid|on-?site|in-?office|in office)\b")
_METRO = re.compile(r"^(greater )?(pittsburgh|pgh)( (metro(politan)?( area)?|area|region))?$")
_SPLIT = re.compile(r"\s*(?:;|\||/|\bor\b|\band\b|\n| - | – | — |\(|\))\s*")
_ABBREVIATIONS = ((r"\bmt\.?\s", "mount "), (r"\bft\.?\s", "fort "), (r"\bst\.?\s", "saint "), (r"\btwp\.?\b", "township"))


class Where(StrEnum):
    IN_AREA = "in_area"
    REMOTE = "remote"
    OUT_OF_AREA = "out_of_area"
    UNKNOWN = "unknown"


def _norm(text: str) -> str:
    text = text.casefold().replace("’", "'")
    for pattern, replacement in _ABBREVIATIONS:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s+(township|borough|city|town|village|municipality)$", "", text.strip(" .,:-"))
    return re.sub(r"\s+", " ", text).strip(" .,:-")


@cache
def _table() -> frozenset[tuple[str, str]]:
    with PLACES.open(encoding="utf-8", newline="") as f:
        return frozenset((_norm(row["name"]), row["state"]) for row in csv.DictReader(f))


def miles_from_downtown(lat: float, lon: float) -> float:
    p1, p2 = math.radians(DOWNTOWN[0]), math.radians(lat)
    dp, dl = p2 - p1, math.radians(lon - DOWNTOWN[1])
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def _segment(segment: str) -> Where:
    """One location, e.g. "Cranberry Twp, PA": in the area, out of it, or unknown."""
    text = _COUNTRY.sub("", _ZIP.sub("", segment.casefold()).strip(" ,.:-")).strip(" ,.:-")
    if not text:
        return Where.UNKNOWN
    if _METRO.match(_norm(text)):
        return Where.IN_AREA
    parts = [p.strip() for p in text.split(",") if p.strip()]
    state = STATES.get(parts[-1].strip(" .")) if len(parts) > 1 else None
    names = parts[:-1] if state else parts
    if state is None and len(parts) == 1:
        # The state without a comma: "Sewickley PA", "Pittsburgh Pennsylvania".
        words = text.rsplit(" ", 2)
        for take in (2, 1):
            tail = " ".join(words[-take:]) if len(words) > take else ""
            if tail in STATES:
                code, rest = STATES[tail], " ".join(words[:-take])
                if len(tail) > 2 or (_norm(rest), code) in _table():
                    # A two-letter code ("OK", "IN") without a comma is trusted only to say
                    # yes: it might not be a state at all.
                    state, names = code, [rest]
                break
    if state is None:
        return Where.IN_AREA if any(_norm(n) == "pittsburgh" for n in names) else Where.UNKNOWN
    # "Strip District, Pittsburgh, PA": a neighbourhood first, then the town. Any name in the
    # area will do.
    for name in names:
        place = _norm(name)
        county = re.fullmatch(r"(.+?) county", place)
        if county and state == "PA" and county[1] in COUNTIES:
            return Where.IN_AREA
        if (place, state) in _table():
            return Where.IN_AREA
    return Where.OUT_OF_AREA


def classify(location: str | None, lat: float | None = None, lon: float | None = None) -> Where:
    """Where a posting is, from its location text and, when given, its coordinates."""
    if lat is not None and lon is not None and miles_from_downtown(lat, lon) <= RADIUS_MILES:
        return Where.IN_AREA
    text = (location or "").strip()
    if not text:
        return Where.UNKNOWN
    lowered = text.casefold()
    remote = bool(_REMOTE.search(lowered))
    cleaned = _HYBRID.sub(" ", _REMOTE.sub(" ", lowered))
    verdicts = [_segment(s) for s in _SPLIT.split(cleaned) if s and s.strip(" ,.:-")]
    if Where.IN_AREA in verdicts:
        return Where.IN_AREA
    if remote:
        return Where.REMOTE
    if Where.OUT_OF_AREA in verdicts:
        return Where.OUT_OF_AREA
    return Where.UNKNOWN
