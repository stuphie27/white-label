from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import router as operations_router
from app.core.config import get_settings
from app.core.security import SecurityHeadersMiddleware
from app.auth.middleware import StaffAccessMiddleware
from app.auth.router import build_router as build_auth_router
from app.auth.service import LoginRateLimiter
from app.payroll_admin import build_payroll_admin_router
from app.payroll_employee import build_payroll_employee_router
from app.db.session import build_engine, build_session_factory
from app.downloads.router import build_downloads_router
from app.downloads.service import expire_due, send_due_reminders
from app.checkout.router import build_checkout_router
from app.checkout.public import build_public_checkout_router
from app.checkout.staff import build_checkout_staff_router
from app.customer_access import build_customer_access_router
from app.customer_home import build_customer_home_router
from app.customer_verification_email import send_customer_verification_code
from app.gallery.router import build_gallery_router
from app.migrations.runner import run_migrations
from app.payments.router import build_payments_router
from app.personal_video.router import build_personal_video_router
from app.profile_requests import build_profile_request_router
from app.availability_requests import build_availability_request_router
from app.payroll_requests import build_payroll_request_router
from app.sync.router import build_sync_router
from app.event_master_sync import event_master_refresh_loop
from app.branding import BRANDS

APP_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=APP_DIR / "templates")
SHARED_UI_DIR = APP_DIR.parent / "shared" / "ui"
CUSTOMER_STATIC_DIR = APP_DIR.parent / "customer-static"
TEMPLATES.env.loader = ChoiceLoader([FileSystemLoader(str(APP_DIR / "templates")), FileSystemLoader(str(SHARED_UI_DIR / "templates"))])


async def _delivery_maintenance_loop(app: FastAPI) -> None:
    """Run secure-delivery expiry and final-24-hour reminders without staff traffic.

    Cloud is customer-only, so reminder processing cannot depend on somebody
    opening a staff deliveries page. Run it hourly while the application is up.
    """
    while True:
        try:
            session_factory = getattr(app.state, "session_factory", None)
            settings = getattr(app.state, "settings", None)
            if session_factory is not None and settings is not None:
                def maintain() -> None:
                    with session_factory() as session:
                        send_due_reminders(session, settings)
                        expire_due(session, app.state.settings)
                await asyncio.to_thread(maintain)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Secure delivery maintenance failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    logging.basicConfig(level=settings.log_level)
    app.state.engine = None
    app.state.session_factory = None
    app.state.database_error = None
    maintenance_task = None
    stuphie_event_master_task = None
    if settings.database_url:
        try:
            engine = build_engine(settings.database_url)
            # Migrations are idempotent and must run in every environment so a
            # newly deployed release cannot serve code against an older schema.
            run_migrations(engine)
            app.state.engine = engine
            app.state.session_factory = build_session_factory(engine)
            maintenance_task = asyncio.create_task(_delivery_maintenance_loop(app))
            if app.state.settings.stuphie_event_master_enabled:
                stuphie_event_master_task = asyncio.create_task(
                    event_master_refresh_loop(
                        app.state.session_factory,
                        app.state.settings,
                    )
                )
        except Exception as exc:
            logging.exception("Database engine initialisation failed")
            app.state.database_error = str(exc)
    yield
    if maintenance_task is not None:
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
    if stuphie_event_master_task is not None:
        stuphie_event_master_task.cancel()
        with suppress(asyncio.CancelledError):
            await stuphie_event_master_task
    if app.state.engine is not None:
        app.state.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Pirouette Cloud",
        version=settings.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.customer_code_sender = send_customer_verification_code
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_host_list,
    )
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(StaffAccessMiddleware)
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="pirouette_customer_session",
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.environment == "production",
    )
    application.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    application.mount("/shared-ui", StaticFiles(directory=SHARED_UI_DIR / "static"), name="shared-ui")
    application.mount("/customer-static", StaticFiles(directory=CUSTOMER_STATIC_DIR), name="customer-static")

    @application.middleware("http")
    async def customer_only_boundary(request: Request, call_next):
        # The online deployment is customer-only. All staff operations remain
        # on the trusted Project Pirouette event Mac.
        path = request.url.path.rstrip("/") or "/"
        allowed_staff_paths = {
            "/staff/login",
            "/staff/logout",
            "/staff/payroll",
        }
        payroll_detail = path.startswith("/staff/payroll/")
        if (
            (path == "/staff" or path.startswith("/staff/"))
            and path not in allowed_staff_paths
            and not payroll_detail
        ):
            return TEMPLATES.TemplateResponse(
                request=request,
                name="404.html",
                context={},
                status_code=404,
            )
        return await call_next(request)

    application.include_router(operations_router)
    application.include_router(
        build_auth_router(
            TEMPLATES,
            LoginRateLimiter(
                settings.login_max_attempts,
                settings.login_window_seconds,
            ),
        )
    )
    application.include_router(build_payroll_admin_router(TEMPLATES))
    application.include_router(build_payroll_employee_router(TEMPLATES))

    # Customer-only Cloud deployment. Staff operations remain exclusively in
    # Project Pirouette on the event Mac. The Cloud app serves public galleries,
    # checkout, payments, secure delivery and authenticated synchronisation APIs.
    application.include_router(build_public_checkout_router(TEMPLATES))
    application.include_router(build_checkout_staff_router(TEMPLATES))
    application.include_router(build_gallery_router(TEMPLATES))
    application.include_router(build_sync_router())
    application.include_router(build_downloads_router(TEMPLATES))
    application.include_router(build_checkout_router())
    application.include_router(build_customer_access_router(TEMPLATES))
    application.include_router(build_customer_home_router(TEMPLATES))
    application.include_router(build_payments_router())
    application.include_router(build_personal_video_router(TEMPLATES))
    application.include_router(build_profile_request_router(TEMPLATES))
    application.include_router(build_availability_request_router(TEMPLATES))
    application.include_router(build_payroll_request_router(TEMPLATES))

    @application.api_route(
        "/staff",
        methods=["GET", "HEAD", "POST"],
        include_in_schema=False,
    )
    @application.api_route(
        "/staff/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def staff_disabled(request: Request, path: str = ""):
        return TEMPLATES.TemplateResponse(
            request=request,
            name="404.html",
            context={},
            status_code=404,
        )

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home(request: Request):
        from datetime import date
        from sqlalchemy import select
        from app.db.models import Event, Gallery
        from app.storage import presigned_get_url

        today = date.today()

        host = str(request.url.hostname or "").strip().lower()

        brand = next(
            (
                item
                for item in BRANDS.values()
                if str(item.get("public_host") or "").lower() == host
            ),
            BRANDS["sophies"],
        )

        brand_id = brand["brand_id"]

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        event_cards = []

        if session_factory is not None:
            with session_factory() as session:
                events = list(
                    session.scalars(
                        select(Event)
                        .where(
                            Event.status.notin_(
                                ("archived",)
                            ),
                            Event.brand_id == brand_id,
                        )
                        .order_by(
                            Event.start_date.asc(),
                            Event.created_at.asc(),
                        )
                    )
                )

                for event in events:
                    gallery = session.scalar(
                        select(Gallery)
                        .where(
                            Gallery.event_id == event.id,
                            Gallery.status == "published",
                            Gallery.visibility == "public",
                            (
                                (Gallery.expires_at.is_(None))
                                | (Gallery.expires_at >= today)
                            ),
                        )
                        .order_by(
                            Gallery.updated_at.desc(),
                            Gallery.created_at.desc(),
                        )
                        .limit(1)
                    )

                    if gallery is None:
                        gallery = session.scalar(
                            select(Gallery)
                            .where(
                                Gallery.event_id == event.id,
                            )
                            .order_by(
                                Gallery.updated_at.desc(),
                                Gallery.created_at.desc(),
                            )
                            .limit(1)
                        )

                    gallery_open = bool(
                        gallery is not None
                        and gallery.status == "published"
                        and gallery.visibility == "public"
                        and (
                            gallery.expires_at is None
                            or gallery.expires_at >= today
                        )
                    )

                    current_event = bool(
                        event.start_date
                        <= today
                        <= event.end_date
                    )

                    if gallery_open:
                        display_state = (
                            "LIVE NOW"
                            if current_event
                            else "VIEW GALLERY"
                        )
                    elif current_event:
                        display_state = "EVENT IN PROGRESS"
                    elif event.end_date < today:
                        display_state = "EVENT FINISHED"
                    else:
                        display_state = "COMING SOON"

                    logo_url = ""

                    logo_path = str(
                        getattr(
                            event,
                            "event_logo_storage_path",
                            "",
                        )
                        or ""
                    ).strip()

                    if logo_path:
                        try:
                            logo_url = presigned_get_url(
                                request.app.state.settings,
                                logo_path,
                                expires_seconds=900,
                            )
                        except Exception:
                            logo_url = ""

                    authoritative_upcoming = bool(
                        getattr(
                            event,
                            "master_source_ref",
                            None,
                        )
                        and event.end_date >= today
                    )

                    public_event_visible = bool(
                        authoritative_upcoming
                        or gallery_open
                    )

                    if not public_event_visible:
                        continue

                    event_cards.append(
                        {
                            "event": event,
                            "gallery": gallery,
                            "gallery_open": gallery_open,
                            "display_state": display_state,
                            "logo_url": logo_url,
                            "current_event": current_event,
                        }
                    )

        event_cards.sort(
            key=lambda card: (
                0
                if (
                    card["event"].status == "live"
                    or (
                        card["event"].start_date
                        <= today
                        <= card["event"].end_date
                    )
                )
                else 1,
                card["event"].start_date,
                str(card["event"].name or "").lower(),
            )
        )

        return TEMPLATES.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "version": request.app.state.settings.version,
                "event_cards": event_cards,
                "today": today,
                "brand": brand,
            },
        )

    @application.get("/robots.txt", include_in_schema=False)
    async def robots():
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("User-agent: *\nDisallow: /staff/\nDisallow: /delivery/\nDisallow: /profile-request/\n")

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
        logging.exception("Unhandled request error on %s", request.url.path, exc_info=exc)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="500.html",
            context={},
            status_code=500,
        )

    return application


app = create_app()
