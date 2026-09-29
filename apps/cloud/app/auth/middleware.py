from __future__ import annotations

import time
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse


class StaffAccessMiddleware(BaseHTTPMiddleware):
    """Enforce the staff/public boundary before any staff route executes."""

    PUBLIC_STAFF_PATHS = {
        "/staff/login",
        "/staff/forgot-password",
        "/staff/forgot-password/sent",
    }

    PUBLIC_STAFF_PREFIXES = (
        "/staff/reset-password/",
    )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"

        public_staff_path = (
            path in self.PUBLIC_STAFF_PATHS
            or any(
                path.startswith(prefix)
                for prefix in self.PUBLIC_STAFF_PREFIXES
            )
        )

        if not path.startswith("/staff") or public_staff_path:
            return await call_next(request)

        authenticated = bool(
            request.session.get("staff_authenticated")
        )

        if not authenticated:
            if request.method in {"GET", "HEAD"}:
                next_path = quote(request.url.path)
                return RedirectResponse(
                    f"/staff/login?next={next_path}",
                    status_code=303,
                )

            return JSONResponse(
                {"detail": "Staff authentication required"},
                status_code=401,
            )

        staff_role = str(
            request.session.get("staff_role", "")
        ).strip()

        # Ordinary employees are deliberately restricted to their
        # self-service journey. Management URLs remain Super Admin only,
        # even when typed directly into the browser.
        # Staff self-service routes.
        #
        # These routes perform their own authenticated staff,
        # CSRF, assignment and ownership checks. They must not
        # be intercepted by the management-only middleware.
        if request.url.path.startswith(
            (
                "/staff/team/my-work",
                "/staff/team/events",
                "/staff/team/profile",
                "/staff/team/my-documents",
            )
        ):
            return await call_next(request)


        if (
            request.url.path.startswith(
                "/staff/team/documents/"
            )
            and request.url.path.endswith(
                "/view"
            )
        ):
            return await call_next(request)

        if staff_role != "super_admin":
            employee_paths = {
                "/staff",
                "/staff/logout",
                "/staff/team/my-work",
                "/staff/team/profile",
                "/staff/team/approved-hours",
            }

            if path not in employee_paths:
                if request.method in {"GET", "HEAD"}:
                    return RedirectResponse(
                        "/staff",
                        status_code=303,
                    )

                return JSONResponse(
                    {"detail": "Super Admin access required"},
                    status_code=403,
                )

        request.session["staff_last_activity"] = int(time.time())
        return await call_next(request)
