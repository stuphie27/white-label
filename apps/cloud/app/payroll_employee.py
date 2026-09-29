from __future__ import annotations

import hashlib
import html
import json
import logging
import mimetypes
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from app.email_design import attach_sophies_logo
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from starlette.background import BackgroundTask

from app.db.models import (
    StaffPayrollLoginToken,
    StaffPayrollSubmission,
)
from app.storage import download_to_temp


LOGGER = logging.getLogger("pirouette.payroll.employee")

SECURE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "object-src 'none'"
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalise_email(value: object) -> str:
    return str(value or "").strip().lower()


def _secure_template(
    templates,
    request: Request,
    name: str,
    context: dict,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
        headers=SECURE_HEADERS,
    )


def _send_login_email(
    settings,
    *,
    email: str,
    secure_link: str,
) -> bool:
    if not settings.smtp_host:
        return False

    plain = f"""Hello,

Use this private link to sign in to your Stuphie payroll history:

{secure_link}

This link expires in 20 minutes and can only be used once.

If you did not request this email, you can ignore it.

Sophie’s Photography
"""

    message = EmailMessage()
    message["From"] = (
        f"{settings.smtp_from_name} "
        f"<{settings.smtp_from_email}>"
    )
    message["To"] = email
    message["Subject"] = "Your Stuphie payroll sign-in link"
    message.set_content(plain)

    safe_link = html.escape(secure_link, quote=True)

    message.add_alternative(
        f"""<!doctype html>
<html>
<body style="margin:0;background:#08080a;color:#f7f7f7;font-family:Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#08080a;padding:28px 12px">
<tr><td align="center">
<table role="presentation" width="620" cellpadding="0" cellspacing="0"
       style="width:100%;max-width:620px;background:#151517;border:1px solid #34343a;border-radius:18px">
<tr><td style="padding:28px"><img src="cid:sophies-logo" alt="Sophie’s Photography" style="display:block;max-width:300px;width:75%;height:auto;margin:0 auto 18px">
<div style="color:#ef3340;font-size:11px;font-weight:800;letter-spacing:3px">
STUPHIE LTD
</div>
<h1 style="margin:8px 0 14px;font-size:28px;color:#fff">
Payroll sign in
</h1>
<p style="color:#c8c8cf;line-height:1.55">
Use the button below to securely view your Stuphie payroll history.
</p>
<p style="margin:28px 0">
<a href="{safe_link}"
   style="display:inline-block;background:#ef3340;color:#fff;text-decoration:none;
          padding:14px 22px;border-radius:10px;font-weight:700">
View my payroll
</a>
</p>
<p style="color:#9999a3;font-size:13px;line-height:1.55">
This private link expires in 20 minutes and can only be used once.
If you did not request it, you can safely ignore this email.
</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>""",
        subtype="html",
    )
    attach_sophies_logo(message)

    try:
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

    except Exception:
        LOGGER.exception(
            "Employee payroll sign-in email delivery failed"
        )
        return False

    return True



def _payload(row: StaffPayrollSubmission) -> dict:
    try:
        value = json.loads(row.payload_json or "{}")
    except Exception:
        value = {}

    if not isinstance(value, dict):
        value = {}

    if not isinstance(value.get("timesheet_entries"), list):
        value["timesheet_entries"] = []

    if not isinstance(value.get("expense_claims"), list):
        value["expense_claims"] = []

    return value


def _money(value: object) -> str:
    try:
        return f"{float(str(value or '0').replace(',', '')):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _minutes(
    start: object,
    finish: object,
    break_minutes: object = 0,
) -> int:
    def clock(value: object) -> int | None:
        text = str(value or "").strip()
        if not text or ":" not in text:
            return None

        try:
            hour, minute = text.split(":", 1)
            return int(hour) * 60 + int(minute[:2])
        except (TypeError, ValueError):
            return None

    start_value = clock(start)
    finish_value = clock(finish)

    if start_value is None or finish_value is None:
        return 0

    if finish_value < start_value:
        finish_value += 24 * 60

    try:
        break_value = max(0, int(break_minutes or 0))
    except (TypeError, ValueError):
        break_value = 0

    return max(
        0,
        finish_value - start_value - break_value,
    )


def _expense_receipt(expense: dict) -> str:
    for key in (
        "receipt_storage_path",
        "receipt_storage_location",
        "receipt_location",
        "receipt_path",
    ):
        value = str(expense.get(key) or "").strip()
        if value:
            return value

    return ""


def _expense_filename(
    expense: dict,
    location: str,
) -> str:
    for key in (
        "receipt_filename",
        "receipt_name",
        "filename",
    ):
        value = str(expense.get(key) or "").strip()
        if value:
            return Path(value).name

    if location:
        return (
            Path(location.split("?", 1)[0]).name
            or "receipt"
        )

    return "receipt"


def _employee_identity(
    request: Request,
) -> tuple[str, str]:
    if not request.session.get(
        "payroll_employee_authenticated"
    ):
        raise HTTPException(401, "Payroll sign-in required.")

    source_ref = str(
        request.session.get(
            "payroll_employee_source_ref",
            "",
        )
        or ""
    ).strip()

    email = _normalise_email(
        request.session.get(
            "payroll_employee_email",
            "",
        )
    )

    if not source_ref and not email:
        raise HTTPException(401, "Payroll sign-in required.")

    return source_ref, email


def _own_submission_query(
    submission_id: str,
    *,
    source_ref: str,
    email: str,
):
    query = select(StaffPayrollSubmission).where(
        StaffPayrollSubmission.id == submission_id
    )

    if source_ref:
        query = query.where(
            StaffPayrollSubmission.staff_source_ref
            == source_ref
        )
    else:
        query = query.where(
            func.lower(
                StaffPayrollSubmission.staff_email
            )
            == email
        )

    return query


def _decorate_submission(
    submission: StaffPayrollSubmission,
) -> tuple[dict, list[dict], list[dict], str, str]:
    payload = _payload(submission)

    timesheets = [
        dict(item)
        for item in payload["timesheet_entries"]
        if isinstance(item, dict)
    ]

    expenses = [
        dict(item)
        for item in payload["expense_claims"]
        if isinstance(item, dict)
    ]

    for item in timesheets:
        item["_paid_minutes"] = _minutes(
            item.get("start_time"),
            item.get("finish_time"),
            item.get("break_minutes"),
        )

    for index, item in enumerate(expenses):
        location = _expense_receipt(item)

        item["_receipt_location"] = location
        item["_receipt_filename"] = (
            _expense_filename(item, location)
        )
        item["_index"] = index
        item["_amount"] = _money(
            item.get("amount")
        )

    paid_minutes = sum(
        item["_paid_minutes"]
        for item in timesheets
    )

    expense_total = sum(
        float(item["_amount"])
        for item in expenses
    )

    view = {
        "id": submission.id,
        "staff_name": submission.staff_name,
        "week_start": submission.week_start,
        "status": submission.status,
        "emailed_at": submission.emailed_at,
        "submitted_at": submission.submitted_at,
        "updated_at": submission.updated_at,
    }

    return (
        view,
        timesheets,
        expenses,
        f"{paid_minutes / 60:.2f}",
        f"{expense_total:.2f}",
    )

def build_payroll_employee_router(templates) -> APIRouter:
    router = APIRouter(tags=["payroll-employee"])

    @router.get(
        "/my-payroll",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def payroll_login(request: Request):
        if request.session.get("payroll_employee_authenticated"):
            return RedirectResponse(
                "/my-payroll/history",
                status_code=303,
            )

        return _secure_template(
            templates,
            request,
            "payroll_employee_login.html",
            {
                "sent": False,
                "error": "",
            },
        )

    @router.post(
        "/my-payroll",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def payroll_login_request(
        request: Request,
        email: str = Form(""),
    ):
        email = _normalise_email(email)

        # Always present the same response to prevent payroll-email
        # enumeration.
        generic_context = {
            "sent": True,
            "error": "",
        }

        if not email or "@" not in email:
            return _secure_template(
                templates,
                request,
                "payroll_employee_login.html",
                generic_context,
            )

        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )
        if factory is None:
            raise HTTPException(503, "Database unavailable.")

        settings = request.app.state.settings

        base_url = str(
            settings.public_base_url or ""
        ).rstrip("/")

        if not base_url.lower().startswith("https://"):
            raise HTTPException(
                503,
                "Secure payroll sign-in requires an HTTPS public URL.",
            )

        with factory() as session:
            matching_email = session.scalar(
                select(StaffPayrollSubmission.staff_email)
                .where(
                    func.lower(
                        StaffPayrollSubmission.staff_email
                    ) == email
                )
                .limit(1)
            )

            if matching_email is None:
                return _secure_template(
                    templates,
                    request,
                    "payroll_employee_login.html",
                    generic_context,
                )

            # A newly requested link invalidates previous unused links
            # for this payroll identity.
            session.execute(
                delete(StaffPayrollLoginToken).where(
                    func.lower(
                        StaffPayrollLoginToken.staff_email
                    ) == email
                )
            )

            token = secrets.token_urlsafe(48)
            expires = _now() + timedelta(minutes=20)

            login_token = StaffPayrollLoginToken(
                staff_email=email,
                token_hash=_hash_token(token),
                expires_at=expires,
            )
            session.add(login_token)
            session.commit()

            secure_link = (
                f"{base_url}/my-payroll/login/{token}"
            )

            sent = _send_login_email(
                settings,
                email=email,
                secure_link=secure_link,
            )

            if not sent:
                # Do not leave a usable bearer credential behind when
                # email delivery failed.
                session.delete(login_token)
                session.commit()

        return _secure_template(
            templates,
            request,
            "payroll_employee_login.html",
            generic_context,
        )

    @router.get(
        "/my-payroll/login/{token}",
        include_in_schema=False,
    )
    async def payroll_magic_login(
        token: str,
        request: Request,
    ):
        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )
        if factory is None:
            raise HTTPException(503, "Database unavailable.")

        with factory() as session:
            row = session.scalar(
                select(StaffPayrollLoginToken).where(
                    StaffPayrollLoginToken.token_hash
                    == _hash_token(token)
                )
            )

            if (
                row is None
                or row.used_at is not None
                or _aware(row.expires_at) <= _now()
            ):
                return _secure_template(
                    templates,
                    request,
                    "payroll_employee_invalid.html",
                    {},
                    status_code=403,
                )

            email = _normalise_email(row.staff_email)

            identity = session.execute(
                select(
                    StaffPayrollSubmission.staff_source_ref,
                    StaffPayrollSubmission.staff_name,
                )
                .where(
                    func.lower(
                        StaffPayrollSubmission.staff_email
                    ) == email
                )
                .order_by(
                    StaffPayrollSubmission.updated_at.desc()
                )
                .limit(1)
            ).first()

            if identity is None:
                row.used_at = _now()
                session.commit()

                return _secure_template(
                    templates,
                    request,
                    "payroll_employee_invalid.html",
                    {},
                    status_code=403,
                )

            source_ref, staff_name = identity

            row.used_at = _now()
            session.commit()

        # These keys are intentionally distinct from staff_authenticated,
        # staff_email and staff_role.
        request.session[
            "payroll_employee_authenticated"
        ] = True
        request.session[
            "payroll_employee_email"
        ] = email
        request.session[
            "payroll_employee_source_ref"
        ] = str(source_ref or "")
        request.session[
            "payroll_employee_name"
        ] = str(staff_name or "")

        return RedirectResponse(
            "/my-payroll/history",
            status_code=303,
            headers=SECURE_HEADERS,
        )

    @router.post(
        "/my-payroll/logout",
        include_in_schema=False,
    )
    async def payroll_logout(request: Request):
        for key in (
            "payroll_employee_authenticated",
            "payroll_employee_email",
            "payroll_employee_source_ref",
            "payroll_employee_name",
        ):
            request.session.pop(key, None)

        return RedirectResponse(
            "/my-payroll",
            status_code=303,
            headers=SECURE_HEADERS,
        )

    @router.get(
        "/my-payroll/history",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def payroll_history(request: Request):
        if not request.session.get(
            "payroll_employee_authenticated"
        ):
            return RedirectResponse(
                "/my-payroll",
                status_code=303,
            )

        source_ref, email = _employee_identity(
            request
        )

        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )
        if factory is None:
            raise HTTPException(
                503,
                "Database unavailable.",
            )

        with factory() as session:
            query = select(
                StaffPayrollSubmission
            )

            if source_ref:
                query = query.where(
                    StaffPayrollSubmission.staff_source_ref
                    == source_ref
                )
            else:
                query = query.where(
                    func.lower(
                        StaffPayrollSubmission.staff_email
                    )
                    == email
                )

            submissions = list(
                session.scalars(
                    query.order_by(
                        StaffPayrollSubmission.week_start.desc(),
                        StaffPayrollSubmission.updated_at.desc(),
                    )
                )
            )

            rows = []

            for submission in submissions:
                payload = _payload(submission)

                paid_minutes = sum(
                    _minutes(
                        item.get("start_time"),
                        item.get("finish_time"),
                        item.get("break_minutes"),
                    )
                    for item in payload[
                        "timesheet_entries"
                    ]
                    if isinstance(item, dict)
                )

                expense_total = sum(
                    float(
                        _money(
                            item.get("amount")
                        )
                    )
                    for item in payload[
                        "expense_claims"
                    ]
                    if isinstance(item, dict)
                )

                rows.append({
                    "id": submission.id,
                    "week_start": submission.week_start,
                    "status": submission.status,
                    "submitted_at": submission.submitted_at,
                    "hours": (
                        f"{paid_minutes / 60:.2f}"
                    ),
                    "expenses": (
                        f"{expense_total:.2f}"
                    ),
                })

        return _secure_template(
            templates,
            request,
            "payroll_employee_history.html",
            {
                "rows": rows,
                "staff_name": request.session.get(
                    "payroll_employee_name",
                    "",
                ),
            },
        )

    @router.get(
        "/my-payroll/submissions/{submission_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def payroll_submission_detail(
        submission_id: str,
        request: Request,
    ):
        if not request.session.get(
            "payroll_employee_authenticated"
        ):
            return RedirectResponse(
                "/my-payroll",
                status_code=303,
            )

        source_ref, email = _employee_identity(
            request
        )

        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )
        if factory is None:
            raise HTTPException(
                503,
                "Database unavailable.",
            )

        with factory() as session:
            submission = session.scalar(
                _own_submission_query(
                    submission_id,
                    source_ref=source_ref,
                    email=email,
                )
            )

            if submission is None:
                raise HTTPException(404)

            (
                view,
                timesheets,
                expenses,
                total_hours,
                total_expenses,
            ) = _decorate_submission(
                submission
            )

        return _secure_template(
            templates,
            request,
            "payroll_employee_detail.html",
            {
                "submission": view,
                "timesheets": timesheets,
                "expenses": expenses,
                "total_hours": total_hours,
                "total_expenses": total_expenses,
            },
        )

    @router.get(
        "/my-payroll/submissions/"
        "{submission_id}/receipt/{expense_index}",
        include_in_schema=False,
    )
    async def payroll_submission_receipt(
        submission_id: str,
        expense_index: int,
        request: Request,
    ):
        if not request.session.get(
            "payroll_employee_authenticated"
        ):
            return RedirectResponse(
                "/my-payroll",
                status_code=303,
            )

        source_ref, email = _employee_identity(
            request
        )

        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )
        if factory is None:
            raise HTTPException(
                503,
                "Database unavailable.",
            )

        with factory() as session:
            submission = session.scalar(
                _own_submission_query(
                    submission_id,
                    source_ref=source_ref,
                    email=email,
                )
            )

            if submission is None:
                raise HTTPException(404)

            expenses = _payload(
                submission
            )["expense_claims"]

            if (
                expense_index < 0
                or expense_index >= len(expenses)
            ):
                raise HTTPException(404)

            expense = expenses[expense_index]

            if not isinstance(expense, dict):
                raise HTTPException(404)

            location = _expense_receipt(expense)

            if not location:
                raise HTTPException(404)

            filename = _expense_filename(
                expense,
                location,
            )

        try:
            temporary = download_to_temp(
                request.app.state.settings,
                location,
                suffix=Path(filename).suffix,
            )
        except Exception as exc:
            raise HTTPException(
                404,
                "Receipt is unavailable.",
            ) from exc

        media_type = (
            mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )

        background = None

        if str(location).startswith("spaces://"):
            background = BackgroundTask(
                temporary.unlink,
                missing_ok=True,
            )

        return FileResponse(
            temporary,
            filename=filename,
            media_type=media_type,
            headers={
                **SECURE_HEADERS,
                "Content-Disposition": (
                    f'inline; filename="{filename}"'
                ),
            },
            background=background,
        )


    return router
