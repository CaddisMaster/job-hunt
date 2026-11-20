"""Application factory: middleware, routers, error handling."""

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.routers import admin, api, auth, invite, main, profile, runs, saved
from app.security import (
    CSRFError,
    HeadAsGetMiddleware,
    LoginRequired,
    SecurityHeadersMiddleware,
    TokenRequired,
    TooManyAttempts,
    csrf_protect,
)
from app.static_files import STATIC_DIR, CachedStaticFiles
from app.templating import templates

log = logging.getLogger("apply")

SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days: a daily app should not nag for a password


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


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
        dependencies=[Depends(csrf_protect)],
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
    app.include_router(auth.router)
    app.include_router(invite.router)
    app.include_router(admin.router)
    app.include_router(profile.router)
    app.include_router(saved.router)
    app.include_router(api.router)
    app.include_router(runs.router)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired) -> Response:
        if request.headers.get("hx-request"):
            # A redirect inside an htmx swap would render the login page into a
            # fragment; HX-Redirect makes the browser navigate instead.
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CSRFError)
    async def _csrf_failed(request: Request, exc: CSRFError) -> Response:
        return _error_page(request, 403, "Your session expired. Reload the page and try again.")

    # The API (#74) answers in JSON, never a page, and never the exception itself.
    @app.exception_handler(TokenRequired)
    async def _token_required(request: Request, exc: TokenRequired) -> Response:
        return JSONResponse({"error": "A valid access token is required."}, status_code=401, headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(TooManyAttempts)
    async def _too_many(request: Request, exc: TooManyAttempts) -> Response:
        return JSONResponse({"error": "Too many attempts with a wrong token. Wait a minute."}, status_code=429)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if _is_api(request):
            return JSONResponse({"error": "Not found." if exc.status_code == 404 else "Request failed."}, status_code=exc.status_code)
        return _error_page(request, exc.status_code, "Not found." if exc.status_code == 404 else "Request failed.")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        # Never show the exception: log it, render error.html.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        if _is_api(request):
            return JSONResponse({"error": "Something went wrong. It has been logged."}, status_code=500)
        return _error_page(request, 500, "Something went wrong. It has been logged.")

    return app


def _error_page(request: Request, status: int, message: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "error.html", {"status": status, "message": message}, status_code=status)


app = create_app()
