from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select, update

from app.auth.service import (
    LoginRateLimiter,
    hash_staff_password,
    new_csrf_token,
    verify_admin_credentials,
    verify_staff_password,
)
from app.db.models import StaffMember, StaffPasswordReset

LOGGER = logging.getLogger("pirouette.auth")
router = APIRouter(prefix="/staff", tags=["staff-auth"])


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def build_router(templates: Jinja2Templates, limiter: LoginRateLimiter) -> APIRouter:
    auth_router = APIRouter(prefix="/staff", tags=["staff-auth"])

    RESET_TTL_MINUTES = 10

    def _reset_digest(raw_token: str) -> str:
        return hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

    def _send_password_reset_email(
        request: Request,
        *,
        email: str,
        raw_token: str,
    ) -> None:
        settings = request.app.state.settings

        if not settings.smtp_host:
            raise RuntimeError(
                "SMTP is not configured for password recovery."
            )

        reset_url = (
            settings.public_base_url.rstrip("/")
            + "/staff/reset-password/"
            + raw_token
        )

        message = EmailMessage()
        message["From"] = (
            f"{settings.smtp_from_name} "
            f"<{settings.smtp_from_email}>"
        )
        message["To"] = email
        message["Subject"] = "Reset your Stuphie Online password"

        message.set_content(
            "A password reset was requested for your "
            "Stuphie Online account.\n\n"
            "Use this secure link within 10 minutes:\n\n"
            f"{reset_url}\n\n"
            "The link can only be used once. "
            "If you did not request this reset, "
            "you can ignore this email."
        )

        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=20,
        ) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()

            if settings.smtp_username:
                smtp.login(
                    settings.smtp_username,
                    settings.smtp_password,
                )

            smtp.send_message(message)

    def _new_auth_csrf(request: Request, key: str) -> str:
        token = new_csrf_token()
        request.session[key] = token
        return token

    def _valid_auth_csrf(
        request: Request,
        *,
        key: str,
        supplied: str,
    ) -> bool:
        expected = str(request.session.get(key, ""))
        return bool(
            expected
            and supplied
            and hmac.compare_digest(expected, supplied)
        )

    @auth_router.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page(request: Request, error: str = ""):
        if request.session.get("staff_authenticated"):
            destination = (
                "/staff"
                if request.session.get("staff_role") == "super_admin"
                else "/staff/team/my-work"
            )
            return RedirectResponse(
                destination,
                status_code=status.HTTP_303_SEE_OTHER,
            )
        token = new_csrf_token()
        request.session["csrf_token"] = token
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": token, "error": error},
        )

    @auth_router.post("/login", include_in_schema=False)
    async def login_submit(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
    ):
        expected_csrf = str(request.session.get("csrf_token", ""))
        if not expected_csrf or not hmac.compare_digest(csrf_token, expected_csrf):
            LOGGER.warning("Rejected staff login with invalid CSRF token from %s", _client_key(request))
            return RedirectResponse(
                "/staff/login?error=" + quote("Your login page expired. Please try again."),
                status_code=status.HTTP_303_SEE_OTHER,
            )

        key = _client_key(request)
        decision = limiter.check(key)
        if not decision.allowed:
            LOGGER.warning("Rate limited staff login from %s", key)
            response = RedirectResponse(
                "/staff/login?error=" + quote("Too many attempts. Please wait before trying again."),
                status_code=status.HTTP_303_SEE_OTHER,
            )
            response.headers["Retry-After"] = str(decision.retry_after_seconds)
            return response

        settings = request.app.state.settings
        authenticated_email = email.strip().lower()

        individual_authenticated = False
        individual_staff_id = ""

        if request.app.state.session_factory is not None:
            with request.app.state.session_factory() as session:
                staff_member = session.scalar(
                    select(StaffMember).where(
                        StaffMember.email.ilike(authenticated_email),
                        StaffMember.active.is_(True),
                        StaffMember.login_enabled.is_(True),
                    )
                )

                if staff_member is not None:
                    individual_authenticated = verify_staff_password(
                        staff_member.password_hash,
                        password,
                    )

                    if individual_authenticated:
                        individual_staff_id = staff_member.id

        legacy_authenticated = False

        if not individual_authenticated:
            legacy_authenticated = verify_admin_credentials(
                authenticated_email,
                password,
                settings.super_admin_email_set,
                settings.admin_password_hash,
            )

        if not individual_authenticated and not legacy_authenticated:
            limiter.record_failure(key)
            LOGGER.warning(
                "Failed staff login for %s from %s",
                authenticated_email,
                key,
            )
            return RedirectResponse(
                "/staff/login?error=" + quote("Email or password was not recognised."),
                status_code=status.HTTP_303_SEE_OTHER,
            )

        limiter.clear(key)
        is_super_admin = (
            authenticated_email in settings.super_admin_email_set
        )

        staff_role = "super_admin" if is_super_admin else "staff"

        request.session.clear()
        request.session["staff_authenticated"] = True
        request.session["staff_email"] = authenticated_email
        request.session["staff_role"] = staff_role

        if individual_staff_id:
            request.session["staff_id"] = individual_staff_id

        request.session["staff_login_at"] = __import__("time").time()
        request.session["staff_last_activity"] = request.session["staff_login_at"]

        LOGGER.info(
            "Successful %s login for %s from %s",
            staff_role,
            authenticated_email,
            key,
        )

        destination = (
            "/staff"
            if is_super_admin
            else "/staff/team/my-work"
        )

        return RedirectResponse(
            destination,
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @auth_router.get(
        "/forgot-password",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def forgot_password_page(request: Request):
        token = _new_auth_csrf(
            request,
            "forgot_password_csrf",
        )

        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={
                "csrf_token": token,
            },
        )


    @auth_router.post(
        "/forgot-password",
        include_in_schema=False,
    )
    async def forgot_password_submit(
        request: Request,
        email: str = Form(...),
        csrf_token: str = Form(...),
    ):
        if not _valid_auth_csrf(
            request,
            key="forgot_password_csrf",
            supplied=csrf_token,
        ):
            return RedirectResponse(
                "/staff/forgot-password",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # Rate-limit reset requests by source IP without disclosing whether
        # an account exists.
        reset_key = "password-reset:" + _client_key(request)
        decision = limiter.check(reset_key)

        if decision.allowed:
            limiter.record_failure(reset_key)

            settings = request.app.state.settings
            normalised_email = email.strip().lower()

            # Password recovery is deliberately restricted to configured
            # Super Admins during the first individual-login release.
            if (
                request.app.state.session_factory is not None
                and normalised_email in settings.super_admin_email_set
            ):
                with request.app.state.session_factory() as session:
                    staff_member = session.scalar(
                        select(StaffMember).where(
                            StaffMember.email.ilike(normalised_email),
                            StaffMember.active.is_(True),
                        )
                    )

                    if staff_member is not None:
                        raw_token = secrets.token_urlsafe(48)
                        digest = _reset_digest(raw_token)
                        now = datetime.now(timezone.utc)

                        # Invalidate all older unused reset challenges.
                        session.execute(
                            update(StaffPasswordReset)
                            .where(
                                StaffPasswordReset.staff_id
                                == staff_member.id,
                                StaffPasswordReset.used_at.is_(None),
                            )
                            .values(used_at=now)
                        )

                        session.add(
                            StaffPasswordReset(
                                staff_id=staff_member.id,
                                token_digest=digest,
                                expires_at=(
                                    now
                                    + timedelta(
                                        minutes=RESET_TTL_MINUTES
                                    )
                                ),
                            )
                        )

                        session.commit()

                        try:
                            _send_password_reset_email(
                                request,
                                email=normalised_email,
                                raw_token=raw_token,
                            )
                        except Exception:
                            LOGGER.exception(
                                "Password reset email delivery failed"
                            )

        # Always show exactly the same response.
        return RedirectResponse(
            "/staff/forgot-password/sent",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @auth_router.get(
        "/forgot-password/sent",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def forgot_password_sent(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="forgot_password_sent.html",
            context={},
        )


    @auth_router.get(
        "/reset-password/{raw_token}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def reset_password_page(
        request: Request,
        raw_token: str,
    ):
        if request.app.state.session_factory is None:
            return templates.TemplateResponse(
                request=request,
                name="reset_password_invalid.html",
                context={},
                status_code=400,
            )

        digest = _reset_digest(raw_token)
        now = datetime.now(timezone.utc)

        with request.app.state.session_factory() as session:
            reset_row = session.scalar(
                select(StaffPasswordReset).where(
                    StaffPasswordReset.token_digest == digest,
                    StaffPasswordReset.used_at.is_(None),
                    StaffPasswordReset.expires_at > now,
                )
            )

            if reset_row is None:
                return templates.TemplateResponse(
                    request=request,
                    name="reset_password_invalid.html",
                    context={},
                    status_code=400,
                )

        csrf_token = _new_auth_csrf(
            request,
            "reset_password_csrf",
        )

        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={
                "csrf_token": csrf_token,
                "raw_token": raw_token,
                "error": "",
            },
        )


    @auth_router.post(
        "/reset-password/{raw_token}",
        include_in_schema=False,
    )
    async def reset_password_submit(
        request: Request,
        raw_token: str,
        password: str = Form(...),
        password_confirm: str = Form(...),
        csrf_token: str = Form(...),
    ):
        def render_error(message: str, code: int = 400):
            token = _new_auth_csrf(
                request,
                "reset_password_csrf",
            )

            return templates.TemplateResponse(
                request=request,
                name="reset_password.html",
                context={
                    "csrf_token": token,
                    "raw_token": raw_token,
                    "error": message,
                },
                status_code=code,
            )

        if not _valid_auth_csrf(
            request,
            key="reset_password_csrf",
            supplied=csrf_token,
        ):
            return render_error(
                "Your reset page expired. Please try the link again."
            )

        if password != password_confirm:
            return render_error(
                "The two passwords do not match."
            )

        if len(password) < 12:
            return render_error(
                "Use at least 12 characters for your new password."
            )

        if request.app.state.session_factory is None:
            return templates.TemplateResponse(
                request=request,
                name="reset_password_invalid.html",
                context={},
                status_code=400,
            )

        digest = _reset_digest(raw_token)
        now = datetime.now(timezone.utc)

        with request.app.state.session_factory() as session:
            reset_row = session.scalar(
                select(StaffPasswordReset).where(
                    StaffPasswordReset.token_digest == digest,
                    StaffPasswordReset.used_at.is_(None),
                    StaffPasswordReset.expires_at > now,
                )
            )

            if reset_row is None:
                return templates.TemplateResponse(
                    request=request,
                    name="reset_password_invalid.html",
                    context={},
                    status_code=400,
                )

            staff_member = session.get(
                StaffMember,
                reset_row.staff_id,
            )

            if (
                staff_member is None
                or not staff_member.active
            ):
                reset_row.used_at = now
                session.commit()

                return templates.TemplateResponse(
                    request=request,
                    name="reset_password_invalid.html",
                    context={},
                    status_code=400,
                )

            staff_member.password_hash = hash_staff_password(
                password
            )
            staff_member.password_changed_at = now
            staff_member.login_enabled = True

            # Consume this token and invalidate every other outstanding
            # reset token for the same account.
            session.execute(
                update(StaffPasswordReset)
                .where(
                    StaffPasswordReset.staff_id
                    == staff_member.id,
                    StaffPasswordReset.used_at.is_(None),
                )
                .values(used_at=now)
            )

            session.commit()

        request.session.pop(
            "reset_password_csrf",
            None,
        )

        LOGGER.info(
            "Individual staff password setup/reset completed"
        )

        # Password setup may have been opened in an existing Super Admin browser session.
        # Clear that identity so the employee always gets a clean sign-in.
        request.session.clear()

        return RedirectResponse(
            "/staff/login?error="
            + quote(
                "Password updated successfully. "
                "You can sign in with your new password."
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @auth_router.post("/logout", include_in_schema=False)
    async def logout(request: Request):
        email = request.session.get("staff_email", "unknown")
        for key in (
            "staff_authenticated",
            "staff_email",
            "staff_role",
            "staff_id",
            "staff_login_at",
            "staff_last_activity",
            "csrf_token",
            "gallery_csrf_token",
            "transfer_csrf_token",
            "payroll_otp_email",
            "payroll_otp_digest",
            "payroll_otp_expires_at",
            "payroll_otp_attempts",
            "payroll_otp_last_sent_at",
            "payroll_otp_verified_at",
            "payroll_otp_last_activity",
        ):
            request.session.pop(key, None)
        LOGGER.info("Staff logout for %s", email)
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    return auth_router
