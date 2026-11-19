"""Application factory: middleware, routers, error handling."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.routers import main
from app.security import HeadAsGetMiddleware, SecurityHeadersMiddleware
from app.static_files import STATIC_DIR, CachedStaticFiles
from app.templating import templates

log = logging.getLogger("apply")

SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days: a daily app should not nag for a password


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Job Hunt",
        version=settings.app_version,
        # No public OpenAPI/Swagger UI: this is an HTML app, and the docs pages
        # need inline scripts the CSP would block anyway.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Middleware runs outermost-last-added: headers wrap everything, including
    # responses produced by the session layer.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="apply_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(HeadAsGetMiddleware)  # outermost: everything inside sees a GET

    app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(main.router)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        return _error_page(request, exc.status_code, "Not found." if exc.status_code == 404 else "Request failed.")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        # Never show the exception: log it, render error.html.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _error_page(request, 500, "Something went wrong. It has been logged.")

    return app


def _error_page(request: Request, status: int, message: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "error.html", {"status": status, "message": message}, status_code=status)


app = create_app()
