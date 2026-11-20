"""Greenhouse's public job board API (#44): every open job on a board, with its
description, in one request.

    GET https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
    {"jobs": [{"id", "title", "absolute_url", "location": {"name"}, "offices": [{"name"}],
               "content": "&lt;p&gt;…"}], "meta": {…}}

`content` is HTML that has itself been entity-encoded, so it's decoded once before
its tags are removed. Greenhouse gives no coordinates and no remote flag; "Remote"
in the location text is read by app/area.py.
"""

from app.sources import FetchError, Found, get_json, plain_text, tidy

LABEL = "Greenhouse"


def url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def parse(payload: object) -> list[Found]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise FetchError("answered in a shape Job Hunt doesn't know")
    found = []
    for job in payload["jobs"]:
        if not isinstance(job, dict) or job.get("id") is None:
            continue
        title, link = tidy(job.get("title"), 300), tidy(job.get("absolute_url"), 1000)
        if not title or not link.startswith("https://"):
            continue
        location = job.get("location") if isinstance(job.get("location"), dict) else {}
        where = tidy(location.get("name"), 500)
        if not where and isinstance(job.get("offices"), list):
            where = tidy("; ".join(o["name"] for o in job["offices"] if isinstance(o, dict) and isinstance(o.get("name"), str)), 500)
        content = job.get("content") if isinstance(job.get("content"), str) else ""
        found.append(Found(str(job["id"])[:200], title, link, where, plain_text(content, escaped=True)))
    return found


def fetch(token: str) -> list[Found]:
    return parse(get_json(url(token)))
