from __future__ import annotations

import hashlib
import html
import json
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from starlette.datastructures import UploadFile

from app.db.models import StaffPayrollSubmission
from app.email_design import attach_sophies_logo
from app.storage import upload_bytes


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


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


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


def _find_live(session, token: str) -> StaffPayrollSubmission | None:
    if not token:
        return None

    row = session.scalar(
        select(StaffPayrollSubmission).where(
            StaffPayrollSubmission.token_hash == _hash_token(token)
        )
    )

    if row is None:
        return None

    if row.status == "submitted":
        return row

    if _aware(row.expires_at) <= _now():
        row.status = "expired"
        row.token_hash = None
        session.commit()
        return None

    if row.status not in {"draft", "emailed"}:
        return None

    return row


def _send_email(settings, row: StaffPayrollSubmission, secure_link: str) -> bool:
    if not settings.smtp_host or not row.staff_email:
        return False

    week_end = row.week_start.fromordinal(row.week_start.toordinal() + 6)

    week_label = (
        f"{row.week_start.strftime('%d %B %Y')} – "
        f"{week_end.strftime('%d %B %Y')}"
    )

    plain = f"""Hello {row.staff_name},

Your Stuphie payroll submission is ready.

Working week:
{week_label}

The Stuphie working week runs Wednesday to Tuesday.

Please use your private link to enter your working hours and expenses:
{secure_link}

If you did not work this week and have no personal expenses to claim, you can ignore this email. If you paid for any work-related expenses using your own money, please add them to your payroll form.

Weekly submission day: Wednesday
Monthly payroll cut-off: 20th of each month

If you have an expense receipt, please upload it with the claim.
If no receipt is available, you will be asked to explain why.

This private link is unique to you. Please do not forward it.

Sophie’s Photography
"""

    message = EmailMessage()
    message["From"] = (
        f"{settings.smtp_from_name} "
        f"<{settings.smtp_from_email}>"
    )
    message["To"] = row.staff_email
    message["Subject"] = (
        f"Your Stuphie timesheet & expenses — "
        f"week ending {week_end.strftime('%d %B')}"
    )
    message.set_content(plain)

    html_body = f"""<!doctype html>
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

<h1 style="margin:8px 0 4px;font-size:28px;color:#fff">
Timesheet &amp; Expenses
</h1>

<p style="color:#fff">
Hello <strong>{html.escape(row.staff_name)}</strong>,
</p>

<p style="color:#c8c8cf;line-height:1.55">
Your Stuphie payroll submission is ready.
</p>

<p style="color:#c8c8cf;line-height:1.55">
If you did not work this week and have no personal expenses to claim, you can ignore this email.
If you paid for any work-related expenses using your own money, please add them to your payroll form.
</p>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#0d0d0f;border:1px solid #303036;border-radius:12px;margin:18px 0">
<tr>
<td style="padding:14px;color:#777">Working week</td>
<td style="padding:14px;color:#fff;font-weight:bold">
{html.escape(week_label)}
</td>
</tr>
<tr>
<td style="padding:14px;color:#777">Submission</td>
<td style="padding:14px;color:#fff">Wednesday</td>
</tr>
<tr>
<td style="padding:14px;color:#777">Payroll cut-off</td>
<td style="padding:14px;color:#fff">20th of each month</td>
</tr>
</table>

<p style="margin:24px 0">
<a href="{html.escape(secure_link, quote=True)}"
   style="display:inline-block;background:#d71920;color:#fff;text-decoration:none;font-weight:bold;padding:14px 20px;border-radius:10px">
Open my payroll submission
</a>
</p>

<p style="color:#999;font-size:13px;line-height:1.5">
Enter your hours and any staff expenses. Upload a receipt where available;
if you do not have a receipt, Stuphie will ask you for a reason.
</p>

<p style="color:#777;font-size:12px;line-height:1.5">
This private link is unique to you. Please do not forward it.
</p>

</td></tr>
</table>

</td></tr>
</table>
</body>
</html>
"""

    message.add_alternative(html_body, subtype="html")
    attach_sophies_logo(message)

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

    return True


def issue_secure_link(
    session,
    settings,
    row: StaffPayrollSubmission,
) -> dict:
    if row.status == "submitted":
        return {
            "source_ref": row.source_ref,
            "status": row.status,
            "emailed": False,
            "already_submitted": True,
        }

    # Never issue a second payroll email for the same weekly submission.
    # The Offline scheduler also checks this, but the Cloud endpoint is the
    # final authority and must remain idempotent if called more than once.
    if row.emailed_at is not None or row.status == "emailed":
        return {
            "source_ref": row.source_ref,
            "status": row.status,
            "emailed": False,
            "already_emailed": True,
            "emailed_at": (
                row.emailed_at.isoformat()
                if row.emailed_at is not None
                else None
            ),
        }

    token = secrets.token_urlsafe(32)

    row.token_hash = _hash_token(token)
    row.status = "draft"
    row.updated_at = _now()

    session.commit()

    secure_link = (
        f"{settings.public_base_url.rstrip('/')}"
        f"/payroll/{token}"
    )

    try:
        sent = _send_email(
            settings,
            row,
            secure_link,
        )
    except Exception:
        row.token_hash = None
        row.status = "email_failed"
        row.updated_at = _now()
        session.commit()
        raise

    if not sent:
        row.token_hash = None
        row.status = "email_failed"
        row.updated_at = _now()
        session.commit()

        return {
            "source_ref": row.source_ref,
            "status": row.status,
            "emailed": False,
        }

    row.status = "emailed"
    row.emailed_at = _now()
    row.updated_at = _now()
    session.commit()

    return {
        "source_ref": row.source_ref,
        "status": row.status,
        "emailed": True,
        "emailed_at": row.emailed_at.isoformat(),
    }



def _payroll_payload(row: StaffPayrollSubmission) -> dict:
    try:
        payload = json.loads(row.payload_json or "{}")
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    if not isinstance(payload.get("timesheet_entries"), list):
        payload["timesheet_entries"] = []

    if not isinstance(payload.get("expense_claims"), list):
        payload["expense_claims"] = []

    payload.setdefault("submission_status", "draft")
    return payload


def _payroll_days(row: StaffPayrollSubmission, payload: dict) -> list[dict]:
    existing = {}

    for entry in payload.get("timesheet_entries", []):
        if isinstance(entry, dict):
            work_date = str(entry.get("work_date") or "").strip()
            if work_date:
                existing[work_date] = entry

    days = []

    for offset in range(7):
        work_date = row.week_start + timedelta(days=offset)
        key = work_date.isoformat()
        entry = existing.get(key, {})

        days.append({
            "work_date": key,
            "day_name": work_date.strftime("%A"),
            "date_label": work_date.strftime("%d %b"),
            "start_time": str(entry.get("start_time") or ""),
            "finish_time": str(entry.get("finish_time") or ""),
            "break_minutes": str(entry.get("break_minutes") or ""),
            "notes": str(entry.get("notes") or ""),
        })

    return days


def _clean_time(value: object) -> str:
    value = str(value or "").strip()
    if not value:
        return ""

    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        return ""

    return parsed.strftime("%H:%M")


def _clean_break_minutes(value: object) -> int:
    try:
        minutes = int(str(value or "0").strip() or "0")
    except ValueError:
        return 0

    return max(0, min(minutes, 1440))


def _clean_amount(value: object) -> str:
    value = str(value or "").strip().replace("£", "").replace(",", "")

    if not value:
        return ""

    try:
        amount = round(float(value), 2)
    except ValueError:
        return ""

    if amount < 0:
        return ""

    return f"{amount:.2f}"


def _payroll_context(
    row: StaffPayrollSubmission,
    token: str,
    *,
    saved: bool = False,
    errors: list[str] | None = None,
) -> dict:
    payload = _payroll_payload(row)

    expense_slots = [{} for _ in range(10)]

    for fallback_index, expense in enumerate(
        payload.get("expense_claims", [])
    ):
        if not isinstance(expense, dict):
            continue

        try:
            slot_index = int(
                expense.get("slot_index", fallback_index)
            )
        except (TypeError, ValueError):
            slot_index = fallback_index

        if 0 <= slot_index < len(expense_slots):
            expense_slots[slot_index] = expense

    return {
        "row": row,
        "token": token,
        "payload": payload,
        "payroll_days": _payroll_days(row, payload),
        "expense_claims": expense_slots,
        "saved": saved,
        "errors": errors or [],
    }


def build_payroll_request_router(templates) -> APIRouter:
    router = APIRouter(tags=["staff-payroll-public"])

    @router.get("/payroll/{token}")
    async def payroll_form(
        token: str,
        request: Request,
        saved: str = "",
    ):
        factory = request.app.state.session_factory

        if factory is None:
            return _secure_template(
                templates,
                request,
                "staff_payroll_closed.html",
                {},
                503,
            )

        with factory() as session:
            row = _find_live(session, token)

            if row is None:
                return _secure_template(
                    templates,
                    request,
                    "staff_payroll_closed.html",
                    {},
                    410,
                )

            return _secure_template(
                templates,
                request,
                "staff_payroll_request.html",
                _payroll_context(
                    row,
                    token,
                    saved=(saved == "1"),
                ),
            )

    @router.post("/payroll/{token}/save")
    async def payroll_save_draft(
        token: str,
        request: Request,
    ):
        factory = request.app.state.session_factory

        if factory is None:
            return _secure_template(
                templates,
                request,
                "staff_payroll_closed.html",
                {},
                503,
            )

        form = await request.form()

        payroll_action = str(
            form.get("payroll_action") or "save"
        ).strip().lower()

        is_submit = payroll_action == "submit"

        with factory() as session:
            row = _find_live(session, token)

            if row is None or row.status == "submitted":
                return _secure_template(
                    templates,
                    request,
                    "staff_payroll_closed.html",
                    {},
                    410,
                )

            timesheet_entries = []

            for offset in range(7):
                work_date = row.week_start + timedelta(days=offset)
                prefix = f"day_{offset}"

                start_time = _clean_time(form.get(f"{prefix}_start_time"))
                finish_time = _clean_time(form.get(f"{prefix}_finish_time"))
                break_minutes = _clean_break_minutes(
                    form.get(f"{prefix}_break_minutes")
                )
                notes = str(
                    form.get(f"{prefix}_notes") or ""
                ).strip()[:1000]

                if start_time or finish_time or break_minutes or notes:
                    timesheet_entries.append({
                        "work_date": work_date.isoformat(),
                        "start_time": start_time,
                        "finish_time": finish_time,
                        "break_minutes": break_minutes,
                        "notes": notes,
                    })

            existing_payload = _payroll_payload(row)
            existing_expense_claims = existing_payload.get(
                "expense_claims",
                [],
            )

            existing_expenses_by_slot = {}

            for fallback_index, expense in enumerate(
                existing_expense_claims
            ):
                if not isinstance(expense, dict):
                    continue

                try:
                    slot_index = int(
                        expense.get(
                            "slot_index",
                            fallback_index,
                        )
                    )
                except (TypeError, ValueError):
                    slot_index = fallback_index

                if 0 <= slot_index < 10:
                    existing_expenses_by_slot[slot_index] = expense

            expense_claims = []

            for index in range(10):
                prefix = f"expense_{index}"
                existing_expense = existing_expenses_by_slot.get(
                    index,
                    {},
                )

                expense_date = str(
                    form.get(f"{prefix}_date") or ""
                ).strip()

                category = str(
                    form.get(f"{prefix}_category") or ""
                ).strip()[:100]

                description = str(
                    form.get(f"{prefix}_description") or ""
                ).strip()[:500]

                amount = _clean_amount(
                    form.get(f"{prefix}_amount")
                )

                receipt_location = existing_expense.get("receipt")
                no_receipt_reason = str(
                    form.get(f"{prefix}_no_receipt_reason")
                    or existing_expense.get("no_receipt_reason")
                    or ""
                ).strip()[:500]

                receipt = form.get(f"{prefix}_receipt")

                if (
                    isinstance(receipt, UploadFile)
                    and receipt.filename
                ):
                    filename = Path(receipt.filename).name
                    suffix = Path(filename).suffix.lower()

                    allowed_suffixes = {
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".webp",
                        ".pdf",
                    }

                    if suffix not in allowed_suffixes:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "Receipts must be JPG, PNG, WebP "
                                "or PDF files."
                            ),
                        )

                    content = await receipt.read(
                        12 * 1024 * 1024 + 1
                    )

                    if len(content) > 12 * 1024 * 1024:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                "Receipt files must be smaller "
                                "than 12 MB."
                            ),
                        )

                    content_type = (
                        receipt.content_type
                        or "application/octet-stream"
                    )

                    object_key = (
                        "staff-payroll-receipts/"
                        f"{row.id}/"
                        f"{index}/"
                        f"{secrets.token_hex(12)}"
                        f"{suffix}"
                    )

                    receipt_location = upload_bytes(
                        request.app.state.settings,
                        object_key,
                        content,
                        content_type,
                    )

                    no_receipt_reason = ""

                if not (
                    expense_date
                    or category
                    or description
                    or amount
                    or receipt_location
                    or no_receipt_reason
                ):
                    continue

                expense_claims.append({
                    "slot_index": index,
                    "expense_date": expense_date,
                    "category": category,
                    "description": description,
                    "amount": amount,
                    "receipt": receipt_location,
                    "no_receipt_reason": no_receipt_reason,
                })

            now = _now()

            payload = _payroll_payload(row)
            payload["timesheet_entries"] = timesheet_entries
            payload["expense_claims"] = expense_claims
            payload["submission_status"] = "draft"
            payload["saved_at"] = now.isoformat()

            row.payload_json = json.dumps(
                payload,
                separators=(",", ":"),
            )
            row.updated_at = now

            # Always save the current draft first. This also ensures
            # any receipt uploaded during a failed final submission
            # remains safely attached to the draft.
            session.commit()

            if is_submit:
                errors: list[str] = []

                # Any day containing working time must have both
                # a start and finish time.
                for entry in timesheet_entries:
                    start_time = str(
                        entry.get("start_time") or ""
                    ).strip()
                    finish_time = str(
                        entry.get("finish_time") or ""
                    ).strip()

                    if bool(start_time) != bool(finish_time):
                        try:
                            work_date = datetime.strptime(
                                str(entry.get("work_date") or ""),
                                "%Y-%m-%d",
                            ).strftime("%A %d %B")
                        except ValueError:
                            work_date = str(
                                entry.get("work_date") or "Timesheet"
                            )

                        errors.append(
                            f"{work_date}: enter both a start "
                            "and finish time."
                        )

                # Every entered expense must be complete and must
                # contain either a receipt or an explanation.
                for expense in expense_claims:
                    slot_number = int(
                        expense.get("slot_index", 0)
                    ) + 1

                    missing = []

                    if not str(
                        expense.get("expense_date") or ""
                    ).strip():
                        missing.append("date")

                    if not str(
                        expense.get("category") or ""
                    ).strip():
                        missing.append("category")

                    if not str(
                        expense.get("description") or ""
                    ).strip():
                        missing.append("description")

                    amount = str(
                        expense.get("amount") or ""
                    ).strip()

                    try:
                        valid_amount = (
                            bool(amount)
                            and float(amount) > 0
                        )
                    except ValueError:
                        valid_amount = False

                    if not valid_amount:
                        missing.append("amount")

                    if missing:
                        errors.append(
                            f"Expense {slot_number}: complete "
                            + ", ".join(missing)
                            + "."
                        )

                    if not (
                        expense.get("receipt")
                        or str(
                            expense.get(
                                "no_receipt_reason"
                            ) or ""
                        ).strip()
                    ):
                        errors.append(
                            f"Expense {slot_number}: upload a "
                            "receipt or explain why you do not "
                            "have one."
                        )

                if errors:
                    # Keep it as a draft and show the staff member
                    # exactly what needs correcting.
                    return _secure_template(
                        templates,
                        request,
                        "staff_payroll_request.html",
                        _payroll_context(
                            row,
                            token,
                            saved=True,
                            errors=errors,
                        ),
                        status_code=400,
                    )

                submitted_at = _now()

                payload["submission_status"] = "submitted"
                payload["submitted_at"] = (
                    submitted_at.isoformat()
                )

                row.payload_json = json.dumps(
                    payload,
                    separators=(",", ":"),
                )
                row.status = "submitted"
                row.submitted_at = submitted_at
                row.updated_at = submitted_at

                session.commit()

                return RedirectResponse(
                    url=f"/payroll/{token}",
                    status_code=303,
                    headers=SECURE_HEADERS,
                )

        return RedirectResponse(
            url=f"/payroll/{token}?saved=1",
            status_code=303,
            headers=SECURE_HEADERS,
        )

    return router

