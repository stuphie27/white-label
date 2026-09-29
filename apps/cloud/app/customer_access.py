from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.customer_consent import (
    CUSTOMER_COPYRIGHT_VERSION,
    CUSTOMER_PRIVACY_VERSION,
    CUSTOMER_TERMS_VERSION,
    record_customer_consent,
)
from app.branding import get_event_brand
from app.db.models import Event
from app.gallery.service import get_gallery_by_slug

from app.customer_identity import (
    CustomerVerificationError,
    create_customer_verification,
    normalise_customer_email,
    remember_verified_customer,
    verify_customer_code,
)


CustomerCodeSender = Callable[[object, str, str], None]


def _csrf(request: Request) -> str:
    token = secrets.token_urlsafe(24)
    request.session["customer_access_csrf"] = token
    return token


def _valid_csrf(request: Request, supplied: str) -> bool:
    expected = str(
        request.session.get("customer_access_csrf", "")
    )
    return bool(
        expected
        and secrets.compare_digest(expected, supplied)
    )


def _safe_next(value: str) -> str:
    value = str(value or "").strip()

    if (
        value.startswith("/")
        and not value.startswith("//")
        and "://" not in value
    ):
        return value

    return "/"


def _brand_for_customer_next(request: Request, next_url: str) -> dict:
    """Resolve white-label brand from the event gallery in the customer next URL."""

    fallback = get_event_brand(None)
    safe_next = _safe_next(next_url)

    if not safe_next.startswith("/g/"):
        return fallback

    slug = safe_next[3:].split("/", 1)[0].split("?", 1)[0].strip()

    if not slug:
        return fallback

    session_factory = getattr(
        request.app.state,
        "session_factory",
        None,
    )

    if session_factory is None:
        return fallback

    try:
        with session_factory() as db:
            gallery = get_gallery_by_slug(db, slug)

            if gallery is None:
                return fallback

            event = db.get(Event, gallery.event_id)

            if event is None:
                return fallback

            return get_event_brand(event)
    except Exception:
        return fallback


def build_customer_access_router(
    templates: Jinja2Templates,
    *,
    code_sender: CustomerCodeSender | None = None,
) -> APIRouter:
    router = APIRouter(tags=["customer-access"])

    @router.get(
        "/customer/register",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def customer_register(
        request: Request,
        next: str = "/",
    ):
        safe_next = _safe_next(next)
        brand = _brand_for_customer_next(request, safe_next)
        request.session["customer_verification_next"] = safe_next

        return templates.TemplateResponse(
            request=request,
            name="customer_register.html",
            context={
                "csrf_token": _csrf(request),
                "next_url": safe_next,
                "brand": brand,
                "error": "",
            },
        )

    @router.post(
        "/customer/register",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def customer_register_submit(
        request: Request,
        first_name: str = Form(""),
        last_name: str = Form(""),
        email: str = Form(...),
        next_url: str = Form("/"),
        csrf_token: str = Form(...),
    ):
        safe_next = _safe_next(next_url)

        if not _valid_csrf(request, csrf_token):
            return templates.TemplateResponse(
                request=request,
                name="customer_register.html",
                context={
                    "csrf_token": _csrf(request),
                    "next_url": safe_next,
                    "brand": _brand_for_customer_next(request, safe_next),
                    "error": "Your form expired. Please try again.",
                },
                status_code=400,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return templates.TemplateResponse(
                request=request,
                name="customer_register.html",
                context={
                    "csrf_token": _csrf(request),
                    "next_url": safe_next,
                    "brand": _brand_for_customer_next(request, safe_next),
                    "error": (
                        "Customer verification is temporarily "
                        "unavailable."
                    ),
                },
                status_code=503,
            )

        try:
            canonical_email = normalise_customer_email(email)

            with session_factory() as db:
                _, code = create_customer_verification(
                    db,
                    secret_key=request.app.state.settings.secret_key,
                    email=canonical_email,
                    first_name=first_name,
                    last_name=last_name,
                )

            sender = (
                code_sender
                or getattr(
                    request.app.state,
                    "customer_code_sender",
                    None,
                )
            )

            if sender is None:
                raise RuntimeError(
                    "Customer verification email sender "
                    "is not configured."
                )

            sender(
                request.app.state.settings,
                canonical_email,
                code,
            )

        except CustomerVerificationError as exc:
            return templates.TemplateResponse(
                request=request,
                name="customer_register.html",
                context={
                    "csrf_token": _csrf(request),
                    "next_url": safe_next,
                    "brand": _brand_for_customer_next(request, safe_next),
                    "error": exc.message,
                },
                status_code=400,
            )
        except Exception:
            return templates.TemplateResponse(
                request=request,
                name="customer_register.html",
                context={
                    "csrf_token": _csrf(request),
                    "next_url": safe_next,
                    "brand": _brand_for_customer_next(request, safe_next),
                    "error": (
                        "We could not send your verification "
                        "code. Please try again."
                    ),
                },
                status_code=503,
            )

        request.session[
            "customer_verification_email"
        ] = canonical_email

        request.session[
            "customer_verification_next"
        ] = safe_next

        return RedirectResponse(
            "/customer/verify",
            status_code=303,
        )

    @router.get(
        "/customer/verify",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def customer_verify(request: Request):
        email = str(
            request.session.get(
                "customer_verification_email",
                "",
            )
        )

        if not email:
            return RedirectResponse(
                "/customer/register",
                status_code=303,
            )

        next_url = str(
            request.session.get(
                "customer_verification_next",
                "/",
            )
        )
        brand = _brand_for_customer_next(request, next_url)

        return templates.TemplateResponse(
            request=request,
            name="customer_verify.html",
            context={
                "csrf_token": _csrf(request),
                "email": email,
                "brand": brand,
                "error": "",
            },
        )

    @router.post(
        "/customer/verify",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def customer_verify_submit(
        request: Request,
        code: str = Form(...),
        csrf_token: str = Form(...),
    ):
        email = str(
            request.session.get(
                "customer_verification_email",
                "",
            )
        )

        if not email:
            return RedirectResponse(
                "/customer/register",
                status_code=303,
            )

        if not _valid_csrf(request, csrf_token):
            return templates.TemplateResponse(
                request=request,
                name="customer_verify.html",
                context={
                    "csrf_token": _csrf(request),
                    "email": email,
                    "brand": _brand_for_customer_next(
                        request,
                        str(request.session.get("customer_verification_next", "/")),
                    ),
                    "error": (
                        "Your form expired. Please try again."
                    ),
                },
                status_code=400,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return templates.TemplateResponse(
                request=request,
                name="customer_verify.html",
                context={
                    "csrf_token": _csrf(request),
                    "email": email,
                    "brand": _brand_for_customer_next(
                        request,
                        str(request.session.get("customer_verification_next", "/")),
                    ),
                    "error": (
                        "Customer verification is temporarily "
                        "unavailable."
                    ),
                },
                status_code=503,
            )

        try:
            with session_factory() as db:
                identity = verify_customer_code(
                    db,
                    secret_key=request.app.state.settings.secret_key,
                    email=email,
                    code=code,
                )

                request.session[
                    "pending_verified_customer_id"
                ] = str(identity.id)

        except CustomerVerificationError as exc:
            return templates.TemplateResponse(
                request=request,
                name="customer_verify.html",
                context={
                    "csrf_token": _csrf(request),
                    "email": email,
                    "error": exc.message,
                },
                status_code=400,
            )

        next_url = _safe_next(
            str(
                request.session.pop(
                    "customer_verification_next",
                    "/",
                )
            )
        )

        request.session.pop(
            "customer_verification_email",
            None,
        )

        request.session[
            "customer_verification_next"
        ] = next_url

        return RedirectResponse(
            "/customer/consent",
            status_code=303,
        )

    @router.get(
        "/customer/consent",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def customer_consent(request: Request):
        identity_id = str(
            request.session.get(
                "pending_verified_customer_id",
                "",
            )
        )

        if not identity_id:
            return RedirectResponse(
                "/customer/register",
                status_code=303,
            )

        next_url = str(
            request.session.get(
                "customer_verification_next",
                "/",
            )
        )
        brand = _brand_for_customer_next(request, next_url)

        return templates.TemplateResponse(
            request=request,
            name="customer_consent.html",
            context={
                "csrf_token": _csrf(request),
                "terms_version": CUSTOMER_TERMS_VERSION,
                "privacy_version": CUSTOMER_PRIVACY_VERSION,
                "copyright_version": CUSTOMER_COPYRIGHT_VERSION,
                "brand": brand,
                "error": "",
            },
        )

    @router.post(
        "/customer/consent",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def customer_consent_submit(
        request: Request,
        accept_terms: str = Form(""),
        accept_privacy: str = Form(""),
        accept_copyright: str = Form(""),
        csrf_token: str = Form(...),
    ):
        identity_id = str(
            request.session.get(
                "pending_verified_customer_id",
                "",
            )
        )

        if not identity_id:
            return RedirectResponse(
                "/customer/register",
                status_code=303,
            )

        next_url = str(
            request.session.get(
                "customer_verification_next",
                "/",
            )
        )

        context = {
            "terms_version": CUSTOMER_TERMS_VERSION,
            "privacy_version": CUSTOMER_PRIVACY_VERSION,
            "copyright_version": CUSTOMER_COPYRIGHT_VERSION,
            "brand": _brand_for_customer_next(request, next_url),
            "error": "",
        }

        if not _valid_csrf(request, csrf_token):
            context["csrf_token"] = _csrf(request)
            context["error"] = (
                "Your form expired. Please try again."
            )
            return templates.TemplateResponse(
                request=request,
                name="customer_consent.html",
                context=context,
                status_code=400,
            )

        accepted = (
            accept_terms == "yes"
            and accept_privacy == "yes"
            and accept_copyright == "yes"
        )

        if not accepted:
            context["csrf_token"] = _csrf(request)
            context["error"] = (
                "Please accept the Terms, Privacy Notice "
                "and Copyright Notice to continue."
            )
            return templates.TemplateResponse(
                request=request,
                name="customer_consent.html",
                context=context,
                status_code=400,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            context["csrf_token"] = _csrf(request)
            context["error"] = (
                "Customer verification is temporarily unavailable."
            )
            return templates.TemplateResponse(
                request=request,
                name="customer_consent.html",
                context=context,
                status_code=503,
            )

        from app.db.models import CustomerIdentity

        with session_factory() as db:
            identity = db.get(
                CustomerIdentity,
                identity_id,
            )

            if identity is None:
                request.session.pop(
                    "pending_verified_customer_id",
                    None,
                )
                return RedirectResponse(
                    "/customer/register",
                    status_code=303,
                )

            record_customer_consent(
                db,
                identity.id,
            )

            remember_verified_customer(
                request.session,
                identity,
            )

        request.session.pop(
            "pending_verified_customer_id",
            None,
        )

        next_url = _safe_next(
            str(
                request.session.pop(
                    "customer_verification_next",
                    "/",
                )
            )
        )

        return RedirectResponse(
            next_url,
            status_code=303,
        )

    return router
