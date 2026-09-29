from __future__ import annotations

import hashlib
import html
import secrets
import smtplib
from datetime import date, datetime, time, timezone
from email.message import EmailMessage

from fastapi import APIRouter, Form, Request
from sqlalchemy import select

from app.db.models import EventAvailabilityRequest
from app.email_design import attach_sophies_logo


SECURE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _secure_template(templates, request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code, headers=SECURE_HEADERS)


def _find_live(session, token: str) -> EventAvailabilityRequest | None:
    if not token:
        return None
    row = session.scalar(select(EventAvailabilityRequest).where(EventAvailabilityRequest.token_hash == _hash_token(token)))
    if row is None or row.status != "sent":
        return None
    if _aware(row.expires_at) <= _now() or row.lock_date <= date.today():
        row.status = "expired"
        row.token_hash = None
        session.commit()
        return None
    return row


def _send_email(settings, row: EventAvailabilityRequest, secure_link: str) -> bool:
    if not settings.smtp_host or not row.staff_email:
        return False
    dates = row.start_date.strftime("%d %B %Y")
    if row.end_date != row.start_date:
        dates += " – " + row.end_date.strftime("%d %B %Y")
    lock = row.lock_date.strftime("%d %B %Y")
    plain = f"""Hello {row.staff_name},

Sophie’s Photography needs your availability for an upcoming event.

Event: {row.event_name}
Date: {dates}
Venue: {row.venue or 'To be confirmed'}
Response deadline: {lock}

Please use your private link to tell Stuphie whether you are available:
{secure_link}

This link is unique to you. Please do not forward it. Your response locks one month before the event; after that deadline any change must be made by management.

Sophie’s Photography
"""
    msg = EmailMessage()
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = row.staff_email
    msg["Subject"] = f"Availability needed — {row.event_name}"
    msg.set_content(plain)
    html_body = f"""<!doctype html>
<html><body style="margin:0;background:#08080a;color:#f7f7f7;font-family:Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#08080a;padding:28px 12px"><tr><td align="center">
<table role="presentation" width="620" cellpadding="0" cellspacing="0" style="width:100%;max-width:620px;background:#151517;border:1px solid #34343a;border-radius:18px">
<tr><td style="padding:26px"><img src="cid:sophies-logo" alt="Sophie’s Photography" style="display:block;max-width:300px;width:75%;height:auto;margin:0 auto 18px">
<div style="color:#ef3340;font-size:11px;font-weight:800;letter-spacing:3px">STUPHIE LTD</div>
<h1 style="margin:8px 0 4px;font-size:28px;color:#fff">Sophie’s Photography</h1>
<p style="margin:0 0 24px;color:#aaa">Staff availability request</p>
<p style="color:#fff">Hello <strong>{html.escape(row.staff_name)}</strong>,</p>
<p style="color:#c8c8cf;line-height:1.55">We need your availability for an upcoming event.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d0f;border:1px solid #303036;border-radius:12px;margin:18px 0">
<tr><td style="padding:14px;color:#777">Event</td><td style="padding:14px;color:#fff;font-weight:bold">{html.escape(row.event_name)}</td></tr>
<tr><td style="padding:14px;color:#777">Date</td><td style="padding:14px;color:#fff">{html.escape(dates)}</td></tr>
<tr><td style="padding:14px;color:#777">Venue</td><td style="padding:14px;color:#fff">{html.escape(row.venue or 'To be confirmed')}</td></tr>
<tr><td style="padding:14px;color:#777">Respond by</td><td style="padding:14px;color:#fff">{html.escape(lock)}</td></tr>
</table>
<p style="margin:24px 0"><a href="{html.escape(secure_link, quote=True)}" style="display:inline-block;background:#d71920;color:#fff;text-decoration:none;font-weight:bold;padding:14px 20px;border-radius:10px">Tell Stuphie my availability</a></p>
<p style="color:#999;font-size:13px;line-height:1.5">This private link is unique to you and closes after successful submission. After the one-month deadline, any change must be made by management.</p>
</td></tr></table></td></tr></table></body></html>"""
    msg.add_alternative(html_body, subtype="html")
    attach_sophies_logo(msg)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)
    return True


def create_remote_request(session, settings, *, source_ref: str, event_source_ref: str, event_name: str, venue: str, start_date: date, end_date: date, lock_date: date, staff_source_ref: str, staff_name: str, staff_email: str, force_send: bool = False) -> dict:
    if not source_ref or not event_source_ref or not staff_source_ref or not staff_email:
        raise ValueError("Event, staff and email details are required")
    if lock_date <= date.today():
        return {"source_ref": source_ref, "status": "locked", "emailed": False, "lock_date": lock_date.isoformat()}

    row = session.scalar(select(EventAvailabilityRequest).where(EventAvailabilityRequest.source_ref == source_ref))
    now = _now()
    if (not force_send) and row is not None and row.status == "submitted":
        return {"source_ref": row.source_ref, "status": row.status, "emailed": False, "already_submitted": True, "lock_date": row.lock_date.isoformat()}
    if (not force_send) and row is not None and row.status == "sent" and _aware(row.expires_at) > now:
        return {"source_ref": row.source_ref, "status": row.status, "emailed": False, "already_sent": True, "lock_date": row.lock_date.isoformat()}

    token = secrets.token_urlsafe(32)
    expires = datetime.combine(lock_date, time.min, tzinfo=timezone.utc)
    if row is None:
        row = EventAvailabilityRequest(source_ref=source_ref)
        session.add(row)
    row.event_source_ref = event_source_ref
    row.event_name = event_name.strip()[:220]
    row.venue = venue.strip()[:300]
    row.start_date = start_date
    row.end_date = end_date
    row.lock_date = lock_date
    row.staff_source_ref = staff_source_ref
    row.staff_name = staff_name.strip()[:200]
    row.staff_email = staff_email.strip()[:320]
    row.token_hash = _hash_token(token)
    row.status = "sent"
    row.availability_status = "unknown"
    row.response_notes = ""
    row.submitted_at = None
    row.expires_at = expires
    row.updated_at = now
    session.commit()

    secure_link = f"{settings.public_base_url.rstrip('/')}/availability-request/{token}"
    try:
        sent = _send_email(settings, row, secure_link)
    except Exception:
        row.status = "email_failed"
        row.token_hash = None
        session.commit()
        raise
    if sent:
        row.emailed_at = now
        session.commit()
    else:
        row.status = "email_failed"
        row.token_hash = None
        session.commit()
    return {"source_ref": row.source_ref, "status": row.status, "emailed": sent, "lock_date": row.lock_date.isoformat()}


def build_availability_request_router(templates) -> APIRouter:
    router = APIRouter(tags=["staff-availability"])

    @router.get("/availability-request/{token}")
    async def availability_form(token: str, request: Request):
        factory = request.app.state.session_factory
        with factory() as session:
            row = _find_live(session, token)
            if row is None:
                return _secure_template(templates, request, "event_availability_closed.html", {}, 410)
            return _secure_template(templates, request, "event_availability_request.html", {"row": row, "token": token})

    @router.post("/availability-request/{token}")
    async def submit_availability(token: str, request: Request, availability_status: str = Form(...), response_notes: str = Form("")):
        status = availability_status.strip().lower()
        if status not in {"available", "unavailable"}:
            status = "unknown"
        factory = request.app.state.session_factory
        with factory() as session:
            row = _find_live(session, token)
            if row is None:
                return _secure_template(templates, request, "event_availability_closed.html", {}, 410)
            if status == "unknown":
                return _secure_template(templates, request, "event_availability_request.html", {"row": row, "token": token, "error": "Please choose Available or Unavailable."}, 422)
            row.availability_status = status
            row.response_notes = response_notes.strip()[:1000]
            row.status = "submitted"
            row.submitted_at = _now()
            row.token_hash = None
            session.commit()
            return _secure_template(templates, request, "event_availability_complete.html", {"row": row})

    return router
