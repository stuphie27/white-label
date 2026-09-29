from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob: https://*.digitaloceanspaces.com; media-src 'self' blob:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://checkout.sumup.com"
        )
        if (
            request.url.path.startswith("/staff")
            or request.url.path.startswith("/delivery/")
            or request.url.path.startswith("/profile-request/")
            or request.url.path.startswith("/availability-request/")
        ):
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            response.headers["Cache-Control"] = "no-store, private"

        if request.url.path.startswith(("/profile-request/", "/availability-request/")):
            response.headers["Referrer-Policy"] = "no-referrer"
        if request.app.state.settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
