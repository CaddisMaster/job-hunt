"""Adzuna's job search API (#45): an aggregator, searched once a night per target title.

    GET https://api.adzuna.com/v1/api/jobs/us/search/<page>
        ?app_id=…&app_key=…&what=<title>&where=Pittsburgh, PA&distance=50&results_per_page=50
    {"count": 123, "results": [{"id", "title", "description", "redirect_url",
      "company": {"display_name"}, "location": {"display_name", "area": [...]},
      "latitude", "longitude", "created"}]}

Checked against developer.adzuna.com on 2026-11-20: the root, app_id/app_key, `what`,
`where`, `results_per_page`, and that `description` is only a snippet. **Not in the
docs that render** (activedocs is a JS app), so to confirm against a live answer once
Sean has keys: the `us` path, `distance` (taken to be km, as the issue says), the
largest page size, and the top-level `count`. If `count` is missing, a full page is
read as "there may be more".

`redirect_url` is Adzuna's own page, which forwards to the ad. Job Hunt can only check
the link it's given, so a LinkedIn or Indeed ad behind that page isn't caught here.

Adzuna's terms (developer.adzuna.com/docs/terms_of_service): each Adzuna ad a page
shows must be labelled "Adzuna", linked to adzuna.com. That's #47's to show.
"""

from urllib.parse import urlencode

from app.config import Settings
from app.sources import FetchError, Found, plain_text, tidy

SOURCE = "adzuna"
LABEL = "Adzuna"
PAGE_SIZE = 50
MAX_PAGES = 4  # per title: 200 jobs within 50 km is far more than one title has here
PAUSE_SECONDS = 2.5  # between requests, well under any per-minute limit
WHERE = "Pittsburgh, PA"
DISTANCE_KM = 50


def missing(settings: Settings) -> str | None:
    """Why this source can't run, or None."""
    if not settings.adzuna_app_id or not settings.adzuna_app_key:
        return "no ADZUNA_APP_ID or ADZUNA_APP_KEY"
    return None


def daily_budget(settings: Settings) -> int:
    return settings.adzuna_daily_requests


def request(title: str, page: int, settings: Settings) -> tuple[str, dict[str, str]]:
    query = urlencode(
        {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "what": title,
            "where": WHERE,
            "distance": DISTANCE_KM,
            "results_per_page": PAGE_SIZE,
            "content-type": "application/json",
        }
    )
    return f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?{query}", {}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _location(location: dict) -> str:
    """"Place, State" from `area` (["US", "Pennsylvania", "Butler County", "Cranberry
    Township"]) when it names a state: app/area.py reads that, and not "Cranberry
    Township, Butler County", which is what `display_name` says. Else `display_name`."""
    levels = [tidy(level, 200) for level in location.get("area") or [] if isinstance(level, str)]
    if len(levels) >= 2 and levels[0] == "US":
        return levels[1] if len(levels) == 2 else f"{levels[-1]}, {levels[1]}"
    return tidy(location.get("display_name"), 500)


def parse(payload: object, page: int) -> tuple[list[Found], bool]:
    """(the page's jobs, whether there may be another page)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise FetchError("answered in a shape Job Hunt doesn't know")
    results = payload["results"]
    found = []
    for job in results:
        if not isinstance(job, dict) or job.get("id") is None:
            continue
        title, link = tidy(job.get("title"), 300), tidy(job.get("redirect_url"), 1000)
        if not title or not link.startswith("https://"):
            continue
        company = job.get("company") if isinstance(job.get("company"), dict) else {}
        location = job.get("location") if isinstance(job.get("location"), dict) else {}
        lat, lon = _number(job.get("latitude")), _number(job.get("longitude"))
        found.append(
            Found(
                external_id=str(job["id"])[:200],
                title=title,
                url=link,
                location=_location(location),
                description=plain_text(job.get("description") if isinstance(job.get("description"), str) else ""),
                latitude=lat if lon is not None else None,
                longitude=lon if lat is not None else None,
                company=tidy(company.get("display_name"), 200),
            )
        )
    count = payload.get("count")
    if isinstance(count, int) and not isinstance(count, bool):
        more = count > page * PAGE_SIZE
    else:
        more = len(results) >= PAGE_SIZE
    return found, more and bool(results)
