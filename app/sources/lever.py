"""Lever's public postings API (#44): every published posting on a board, in one
request.

    GET https://api.lever.co/v0/postings/<token>?mode=json
    [{"id", "text", "hostedUrl", "workplaceType": "remote" | "hybrid" | "onsite",
      "categories": {"location", "allLocations": [...]},
      "description" (HTML), "lists": [{"text", "content" (HTML <li>s)}], "additional" (HTML)}]

Without `limit`, Lever returns every posting. The description is Lever's three parts
in the order its own page shows them.
"""

from app.sources import FetchError, Found, get_json, plain_text, tidy

LABEL = "Lever"


def url(token: str) -> str:
    return f"https://api.lever.co/v0/postings/{token}?mode=json"


def _html(job: dict) -> str:
    parts = [job.get("description") if isinstance(job.get("description"), str) else ""]
    for item in job.get("lists") or []:
        if isinstance(item, dict):
            heading = item.get("text") if isinstance(item.get("text"), str) else ""
            content = item.get("content") if isinstance(item.get("content"), str) else ""
            parts.append(f"<h3>{heading}</h3><ul>{content}</ul>")
    parts.append(job.get("additional") if isinstance(job.get("additional"), str) else "")
    return "\n".join(parts)


def parse(payload: object) -> list[Found]:
    if not isinstance(payload, list):
        raise FetchError("answered in a shape Job Hunt doesn't know")
    found = []
    for job in payload:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            continue
        title, link = tidy(job.get("text"), 300), tidy(job.get("hostedUrl"), 1000)
        if not title or not link.startswith("https://"):
            continue
        categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
        places = [p for p in categories.get("allLocations") or [] if isinstance(p, str)]
        if not places and isinstance(categories.get("location"), str):
            places = [categories["location"]]
        remote = job.get("workplaceType") == "remote"
        found.append(Found(job["id"][:200], title, link, tidy("; ".join(places), 500), plain_text(_html(job)), remote))
    return found


def fetch(token: str) -> list[Found]:
    return parse(get_json(url(token)))
