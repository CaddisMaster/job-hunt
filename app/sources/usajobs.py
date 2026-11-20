"""USAJobs' search API (#45): federal jobs, searched once a night per target title.

    GET https://data.usajobs.gov/api/search?Keyword=<title>&LocationName=Pittsburgh, Pennsylvania
        &Radius=50&ResultsPerPage=500&Page=<n>
    Headers: User-Agent: <the email the key was issued to>, Authorization-Key: <key>
    {"SearchResult": {"SearchResultCountAll": 12, "SearchResultItems": [
      {"MatchedObjectId": "…", "MatchedObjectDescriptor": {"PositionTitle", "PositionURI",
        "OrganizationName", "DepartmentName", "PositionLocationDisplay",
        "PositionLocation": [{"LocationName", "Latitude", "Longitude"}],
        "QualificationSummary", "UserArea": {"Details": {"JobSummary"}}}}]}}

Checked against developer.usajobs.gov on 2026-11-20: the endpoint, the headers, the
500-row page cap, the fields above. `MatchedObjectId` is a number in the docs' table
and a string in their example; both are read. **The docs don't give Radius's unit**;
it's taken to be miles, as the issue says. The response has no remote flag, so
"remote" is read from the location text by app/area.py.

`PositionURI` is the official announcement on usajobs.gov, so that's the link.
USAJobs' public data is openly licensed; its terms ask that the key not be shared.
"""

from urllib.parse import urlencode

from app import area
from app.config import Settings
from app.sources import FetchError, Found, clip, plain_text, tidy

SOURCE = "usajobs"
LABEL = "USAJobs"
PAGE_SIZE = 500
MAX_PAGES = 2
PAUSE_SECONDS = 1.0
WHERE = "Pittsburgh, Pennsylvania"
RADIUS_MILES = 50


def missing(settings: Settings) -> str | None:
    if not settings.usajobs_api_key or not settings.usajobs_user_agent:
        return "no USAJOBS_API_KEY or USAJOBS_USER_AGENT"
    return None


def daily_budget(settings: Settings) -> int:
    return settings.usajobs_daily_requests


def request(title: str, page: int, settings: Settings) -> tuple[str, dict[str, str]]:
    query = urlencode(
        {"Keyword": title, "LocationName": WHERE, "Radius": RADIUS_MILES, "ResultsPerPage": PAGE_SIZE, "Page": page}
    )
    headers = {"User-Agent": settings.usajobs_user_agent, "Authorization-Key": settings.usajobs_api_key}
    return f"https://data.usajobs.gov/api/search?{query}", headers


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if isinstance(value, int | float | str) else None
    except ValueError:
        return None


def _place(locations: object) -> tuple[str, float | None, float | None]:
    """The location text (every place, joined) and one place's coordinates: the first
    in the area if any is, else none, so the text decides."""
    places = [p for p in locations if isinstance(p, dict)] if isinstance(locations, list) else []
    names = [tidy(p.get("LocationName"), 200) for p in places]
    for p in places:
        lat, lon = _number(p.get("Latitude")), _number(p.get("Longitude"))
        if lat is not None and lon is not None and area.miles_from_downtown(lat, lon) <= area.RADIUS_MILES:
            return "; ".join(n for n in names if n), lat, lon
    return "; ".join(n for n in names if n), None, None


def parse(payload: object, page: int) -> tuple[list[Found], bool]:
    result = payload.get("SearchResult") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get("SearchResultItems"), list):
        raise FetchError("answered in a shape Job Hunt doesn't know")
    items = result["SearchResultItems"]
    found = []
    for item in items:
        if not isinstance(item, dict) or item.get("MatchedObjectId") in (None, ""):
            continue
        job = item.get("MatchedObjectDescriptor")
        if not isinstance(job, dict):
            continue
        title, link = tidy(job.get("PositionTitle"), 300), tidy(job.get("PositionURI"), 1000)
        if not title or not link.startswith("https://"):
            continue
        where, lat, lon = _place(job.get("PositionLocation"))
        display = tidy(job.get("PositionLocationDisplay"), 500)
        if lat is None and display and (not where or "multiple" not in display.casefold()):
            where = display  # "Pittsburgh, Pennsylvania"; "Multiple Locations" says less than the list
        user_area = job.get("UserArea") if isinstance(job.get("UserArea"), dict) else {}
        details = user_area.get("Details") if isinstance(user_area.get("Details"), dict) else {}
        parts = [details.get("JobSummary"), job.get("QualificationSummary")]
        description = "\n\n".join(plain_text(p) for p in parts if isinstance(p, str) and p.strip())
        found.append(
            Found(
                external_id=str(item["MatchedObjectId"])[:200],
                title=title,
                url=link,
                location=clip(where, 500),
                description=clip(description, 50_000),
                latitude=lat,
                longitude=lon,
                company=tidy(job.get("OrganizationName"), 200) or tidy(job.get("DepartmentName"), 200),
            )
        )
    total = result.get("SearchResultCountAll")
    more = total > page * PAGE_SIZE if isinstance(total, int) and not isinstance(total, bool) else len(items) >= PAGE_SIZE
    return found, more and bool(items)
