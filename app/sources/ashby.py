"""Ashby's public job board API (#44): every listed job on a board, in one request.

    GET https://api.ashbyhq.com/posting-api/job-board/<token>
    {"jobs": [{"id", "title", "jobUrl", "location", "secondaryLocations": [{"location"}],
               "isRemote", "workplaceType": "Remote" | "Hybrid" | "OnSite", "isListed", "descriptionHtml", "descriptionPlain",
               "address": {"postalAddress": {"addressLocality", "addressRegion"}}}]}

`isRemote` is true for hybrid jobs too, so `workplaceType` says whether a job is
remote. A job's location is often a label ("New York, NY (HQ)", "Europe"), so the postal
address's town and state are added beside it when Ashby gives them. Unlisted jobs
are skipped.
"""

from app.sources import FetchError, Found, get_json, plain_text, tidy

LABEL = "Ashby"


def url(token: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{token}"


def _address(holder: object) -> str:
    if not isinstance(holder, dict) or not isinstance(holder.get("address"), dict):
        return ""
    postal = holder["address"].get("postalAddress")
    if not isinstance(postal, dict):
        return ""
    town, state = postal.get("addressLocality"), postal.get("addressRegion")
    if isinstance(town, str) and isinstance(state, str) and town.strip() and state.strip():
        return f"{town.strip()}, {state.strip()}"
    return ""


def _places(job: dict) -> list[str]:
    places = []
    for holder in [job, *(job.get("secondaryLocations") or [])]:
        if not isinstance(holder, dict):
            continue
        for place in (holder.get("location"), _address(holder)):
            if isinstance(place, str) and place.strip() and place.strip() not in places:
                places.append(place.strip())
    return places


def parse(payload: object) -> list[Found]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise FetchError("answered in a shape Job Hunt doesn't know")
    found = []
    for job in payload["jobs"]:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str) or job.get("isListed") is False:
            continue
        title, link = tidy(job.get("title"), 300), tidy(job.get("jobUrl"), 1000)
        if not title or not link.startswith("https://"):
            continue
        if isinstance(job.get("descriptionHtml"), str):
            description = plain_text(job["descriptionHtml"])
        else:
            description = plain_text(job.get("descriptionPlain") if isinstance(job.get("descriptionPlain"), str) else "")
        # isRemote is also true for hybrid jobs (Ramp's, 2026-11-19): the workplace type
        # decides, and isRemote only stands in when there's no type.
        kind = job.get("workplaceType")
        remote = kind == "Remote" if isinstance(kind, str) else job.get("isRemote") is True
        found.append(Found(job["id"][:200], title, link, tidy("; ".join(_places(job)), 500), description, remote))
    return found


def fetch(token: str) -> list[Found]:
    return parse(get_json(url(token)))
