"""Security headers and the HEAD → GET shim. Logins, sessions and CSRF arrive
with #2."""

import secrets

from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings


# ---------------------------------------------------------------------------
# HEAD → GET. FastAPI's APIRoute doesn't answer HEAD for a GET route, and uptime
# monitors use HEAD (Orbit Ops #9). Serve it as the GET and drop the body.
# A pure ASGI middleware: it rewrites the scope and never buffers a response.
# ---------------------------------------------------------------------------
class HeadAsGetMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message):
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


# ---------------------------------------------------------------------------
# Security headers, with a fresh CSP nonce per request for inline scripts.
# Never add 'unsafe-inline' or 'unsafe-eval'.
# ---------------------------------------------------------------------------
def build_csp(nonce: str) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = build_csp(nonce)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if get_settings().cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
