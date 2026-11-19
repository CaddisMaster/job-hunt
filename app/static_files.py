"""Static files, versioned so a release reaches every browser (Orbit Ops #92).

Every URL the templates emit carries `?v=`: the release version in production,
the file's mtime in development. A versioned request is cached for a year; an
unversioned one must revalidate.
"""

from pathlib import Path
from urllib.parse import parse_qs

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app.config import get_settings

STATIC_DIR = Path(__file__).parent / "static"

IMMUTABLE = "public, max-age=31536000, immutable"


def static_url(path: str) -> str:
    version = get_settings().app_version
    if version == "dev":
        try:
            version = str(int((STATIC_DIR / path).stat().st_mtime))
        except OSError:
            version = "0"  # a typo'd path 404s on its own; the page still renders
    return f"/static/{path}?v={version}"


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):  # never pin a 404 for a year
            versioned = "v" in parse_qs(scope["query_string"].decode())
            response.headers["Cache-Control"] = IMMUTABLE if versioned else "no-cache"
        return response
