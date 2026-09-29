from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.availability_requests import build_availability_request_router
from app.auth.middleware import StaffAccessMiddleware
from app.auth.router import build_router as build_auth_router
from app.auth.service import LoginRateLimiter
from app.core.config import get_settings
from app.core.security import SecurityHeadersMiddleware
from app.db.session import build_engine, build_session_factory
from app.events.router import build_events_router
from app.event_handoff import build_event_handoff_router
from app.sync.router import build_sync_router
from app.team import build_team_router
from app.profile_requests import build_profile_request_router
from app.staff import build_staff_router
from app.migrations.runner import run_migrations
from app.payroll_admin import build_payroll_admin_router
from app.payroll_employee import build_payroll_employee_router
from app.payroll_requests import build_payroll_request_router


APP_DIR = __import__("pathlib").Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=APP_DIR / "templates")

SHARED_UI_DIR = APP_DIR.parent / "shared" / "ui"

TEMPLATES.env.loader = ChoiceLoader(
    [
        FileSystemLoader(str(APP_DIR / "templates")),
        FileSystemLoader(str(SHARED_UI_DIR / "templates")),
    ]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    app.state.settings = settings
    app.state.engine = None
    app.state.session_factory = None
    app.state.database_error = None

    logging.basicConfig(level=settings.log_level)

    if settings.database_url:
        try:
            engine = build_engine(settings.database_url)

            # Stuphie Online uses the same versioned database migration
            # framework as the existing Cloud services.
            run_migrations(engine)

            app.state.engine = engine
            app.state.session_factory = build_session_factory(engine)

        except Exception as exc:
            logging.exception("Stuphie Online database initialisation failed")
            app.state.database_error = str(exc)

    yield

    if app.state.engine is not None:
        app.state.engine.dispose()


def create_online_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title="Stuphie Online",
        version=settings.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    application.state.settings = settings

    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_host_list,
    )
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(StaffAccessMiddleware)

    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="stuphie_online_session",
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.environment == "production",
    )

    application.mount(
        "/static",
        StaticFiles(directory=APP_DIR / "static"),
        name="static",
    )

    application.mount(
        "/shared-ui",
        StaticFiles(directory=SHARED_UI_DIR / "static"),
        name="shared-ui",
    )

    # Authentication
    application.include_router(
        build_auth_router(
            TEMPLATES,
            LoginRateLimiter(
                settings.login_max_attempts,
                settings.login_window_seconds,
            ),
        )
    )

    # Stuphie Online management dashboard
    application.include_router(build_availability_request_router(TEMPLATES))
    application.include_router(build_staff_router(TEMPLATES))

    # Stuphie Online Operations
    application.include_router(build_events_router(TEMPLATES))

    # Read-only Online -> Offline event handoff.
    application.include_router(build_event_handoff_router())
    application.include_router(build_sync_router())
    application.include_router(build_team_router(TEMPLATES))
    application.include_router(build_profile_request_router(TEMPLATES))

    # Existing proven staff/payroll functionality.
    application.include_router(build_payroll_admin_router(TEMPLATES))
    application.include_router(build_payroll_employee_router(TEMPLATES))
    application.include_router(build_payroll_request_router(TEMPLATES))

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home(request: Request):
        if not request.session.get("staff_authenticated"):
            return RedirectResponse("/staff/login", status_code=303)

        if request.session.get("staff_role") == "super_admin":
            return RedirectResponse("/staff", status_code=303)

        return RedirectResponse(
            "/staff/team/my-work",
            status_code=303,
        )

    @application.get("/health", include_in_schema=False)
    async def health(request: Request):
        database_ready = request.app.state.session_factory is not None

        return {
            "service": "stuphie-online",
            "status": "ok" if database_ready else "database-unavailable",
            "database": database_ready,
            "version": request.app.state.settings.version,
        }

    @application.exception_handler(404)
    async def not_found(request: Request, exc):
        return TEMPLATES.TemplateResponse(
            request=request,
            name="404.html",
            context={},
            status_code=404,
        )

    @application.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        logging.exception(
            "Unhandled Stuphie Online request on %s",
            request.url.path,
            exc_info=exc,
        )

        return TEMPLATES.TemplateResponse(
            request=request,
            name="500.html",
            context={},
            status_code=500,
        )

    return application


app = create_online_app()
