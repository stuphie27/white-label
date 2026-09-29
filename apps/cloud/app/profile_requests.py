from __future__ import annotations

import hashlib
import html
import json
import secrets
import smtplib

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from app.email_design import attach_sophies_logo
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.db.models import StaffMember, StaffProfileRequest
from app.storage import delete, upload_bytes
from app.staff_photos import normalise_staff_photo


PROFILE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), "
        "payment=(), usb=()"
    ),
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self' data:; "
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
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


def _secure_template(
    templates,
    request: Request,
    template_name: str,
    context: dict,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
        status_code=status_code,
        headers=PROFILE_HEADERS,
    )


def _find_live_request(
    session,
    token: str,
) -> StaffProfileRequest | None:

    if not token:
        return None

    row = session.scalar(
        select(StaffProfileRequest).where(
            StaffProfileRequest.token_hash
            == _hash_token(token)
        )
    )

    if row is None:
        return None

    if row.status != "sent":
        return None

    if _aware(row.expires_at) <= _now():
        row.status = "expired"
        row.token_hash = None
        session.commit()

        return None

    return row


# ============================================================

def _sync_staff_master_from_profile(
    session,
    row: StaffProfileRequest,
) -> StaffMember:
    """
    Create or update the permanent Staff Master identity
    from the existing Staff Profile record.

    staff_source_ref is shared by profile, availability,
    payroll and event staffing.
    """
    source_ref = str(row.source_ref or "").strip()

    if not source_ref:
        raise ValueError(
            "Staff Profile source_ref is required "
            "for Staff Master sync."
        )

    member = session.scalar(
        select(StaffMember).where(
            StaffMember.staff_source_ref == source_ref
        )
    )

    if member is None:
        member = StaffMember(
            staff_source_ref=source_ref,
        )
        session.add(member)

    member.staff_code = str(
        row.staff_code or ""
    )[:80]

    member.staff_name = str(
        row.staff_name or ""
    )[:200]

    member.preferred_name = str(
        row.preferred_name or ""
    )[:200]

    member.email = str(
        row.staff_email or ""
    ).strip().lower()[:320]

    member.default_role = (
        str(row.role or "").strip()[:160]
        or "Photography Staff"
    )

    member.active = True

    # Staff Hub permanent profile.
    #
    # The complete profile payload is private. Only telephone and
    # photograph are also used by the restricted Staff Directory.
    try:
        payload = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}

    member.telephone = str(
        payload.get("telephone") or ""
    ).strip()[:80]

    member.private_profile_json = (
        row.payload_json or ""
    )

    member.photo_storage_path = str(
        row.photo_storage_path or ""
    )

    member.photo_filename = str(
        row.photo_filename or ""
    )[:240]

    member.photo_content_type = str(
        row.photo_content_type or ""
    )[:120]

    member.profile_completed_at = (
        row.submitted_at or _now()
    )

    session.flush()

    return member


# BRANDED STAFF INVITATION EMAIL
# ============================================================

def _staff_email_html(
    *,
    staff_name: str,
    staff_code: str,
    secure_link: str,
    expires_text: str,
) -> str:

    safe_name = html.escape(staff_name)
    safe_code = html.escape(staff_code)
    safe_link = html.escape(secure_link, quote=True)
    safe_expiry = html.escape(expires_text)

    return f"""<!doctype html>
<html>
<body style="
    margin:0;
    padding:0;
    background:#071018;
    font-family:Arial,Helvetica,sans-serif;
    color:#f4f7f9;
">

<table role="presentation"
       width="100%"
       cellspacing="0"
       cellpadding="0"
       border="0"
       style="background:#071018;padding:28px 12px;">

<tr>
<td align="center">

<table role="presentation"
       width="100%"
       cellspacing="0"
       cellpadding="0"
       border="0"
       style="
          max-width:640px;
          background:#111c25;
          border:1px solid #31485a;
          border-radius:18px;
          overflow:hidden;
       ">

<tr>
<td align="center"
    style="
      padding:18px 24px;
      background:#ffffff;
    ">

<img
  src="cid:sophies-logo"
  alt="Sophie's Photography"
  style="
    display:block;
    max-width:210px;
    width:58%;
    height:auto;
    margin:0 auto;
  "
>

</td>
</tr>


<tr>
<td style="padding:30px 30px 18px;text-align:center;">

<div style="
  color:#97a7b3;
  font-size:12px;
  font-weight:700;
  letter-spacing:1.5px;
">
STAFF ONBOARDING
</div>

<h1 style="
  margin:8px 0 10px;
  font-size:26px;
  color:#ffffff;
">
Complete Your Staff Profile
</h1>

<p style="
  margin:0;
  color:#b4c0c9;
  font-size:15px;
  line-height:1.6;
">
Hello {safe_name},
</p>

<p style="
  margin:10px auto 0;
  max-width:500px;
  color:#b4c0c9;
  font-size:15px;
  line-height:1.6;
">
Sophie’s Photography has created a private staff profile
request for you.
</p>

</td>
</tr>


<tr>
<td style="padding:8px 30px 20px;">

<table role="presentation"
       width="100%"
       cellspacing="0"
       cellpadding="0"
       border="0"
       style="
          background:#10271a;
          border:1px solid #3c8256;
          border-radius:14px;
       ">

<tr>
<td style="padding:20px;">

<div style="
  color:#78f0a0;
  font-weight:800;
  font-size:12px;
  letter-spacing:1.4px;
">
🔒 PRIVATE STAFF PROFILE
</div>

<h2 style="
  margin:7px 0 12px;
  color:#ffffff;
  font-size:18px;
">
This secure link belongs only to you.
</h2>

<table role="presentation"
       width="100%"
       cellspacing="0"
       cellpadding="0"
       border="0">

<tr>
<td style="padding:5px 0;color:#c1ccc5;font-size:14px;">
✓ Secure HTTPS profile page
</td>
</tr>

<tr>
<td style="padding:5px 0;color:#c1ccc5;font-size:14px;">
✓ Expires automatically after 14 days
</td>
</tr>

<tr>
<td style="padding:5px 0;color:#c1ccc5;font-size:14px;">
✓ Becomes unusable after successful submission
</td>
</tr>

<tr>
<td style="padding:5px 0;color:#c1ccc5;font-size:14px;">
✓ Do not forward or share this email or link
</td>
</tr>

</table>

</td>
</tr>
</table>

</td>
</tr>


<tr>
<td align="center"
    style="padding:8px 30px 24px;">

<a href="{safe_link}"
   style="
      display:inline-block;
      padding:15px 26px;
      background:#be1e2d;
      color:#ffffff;
      text-decoration:none;
      border-radius:8px;
      font-size:14px;
      font-weight:800;
      letter-spacing:.5px;
   ">
COMPLETE MY STAFF PROFILE
</a>

</td>
</tr>


<tr>
<td style="padding:0 30px 20px;">

<table role="presentation"
       width="100%"
       cellspacing="0"
       cellpadding="0"
       border="0"
       style="
          background:#0a141d;
          border-radius:10px;
       ">

<tr>
<td style="
  padding:14px 16px;
  color:#9fafbb;
  font-size:13px;
  line-height:1.7;
">

<strong style="color:#ffffff;">
Staff ID:
</strong>
{safe_code}

<br>

<strong style="color:#ffffff;">
Link expires:
</strong>
{safe_expiry}

</td>
</tr>

</table>

</td>
</tr>


<tr>
<td style="padding:0 30px 26px;">

<p style="
  margin:0 0 10px;
  color:#c6d0d7;
  font-size:13px;
  line-height:1.6;
">
<strong style="color:#ffffff;">
Important security information
</strong>
</p>

<p style="
  margin:0;
  color:#9faeb8;
  font-size:13px;
  line-height:1.7;
">
Sophie’s Photography will never ask you to reply to an email
with medical information, allergy information, emergency-contact
details or other confidential staff information.
</p>

<p style="
  margin:10px 0 0;
  color:#9faeb8;
  font-size:13px;
  line-height:1.7;
">
Enter confidential information only through the secure profile
page opened by the red button above.
</p>

<p style="
  margin:10px 0 0;
  color:#9faeb8;
  font-size:13px;
  line-height:1.7;
">
Health, allergy and support information is optional and is
collected only for staff welfare and emergency purposes.
It will never appear on your staff ID pass or ordinary event
timesheets.
</p>

<p style="
  margin:10px 0 0;
  color:#e7bd75;
  font-size:13px;
  line-height:1.7;
">
If you think this email or link has been shared with somebody
else, do not use it. Contact Sophie’s Photography and we will
revoke it and issue a new one.
</p>

</td>
</tr>


<tr>
<td align="center"
    style="
      padding:16px 20px;
      background:#080f15;
      color:#8d9ba6;
      font-size:11px;
      letter-spacing:.7px;
    ">

<strong style="color:#ffffff;">
OFFICIAL PHOTOGRAPHY STAFF
</strong>

<br>

SOPHIE’S PHOTOGRAPHY

</td>
</tr>

</table>

</td>
</tr>

</table>

</body>
</html>
"""


def _send_staff_profile_email(
    settings,
    *,
    to: str,
    staff_name: str,
    staff_code: str,
    secure_link: str,
    expires: datetime,
) -> bool:

    if not settings.smtp_host:
        return False

    expires_text = (
        f"{expires:%d %B %Y at %H:%M UTC}"
    )

    plain_text = f"""Hello {staff_name},

Sophie’s Photography has created a private staff profile request for you.

Please use the secure link below to provide your contact details,
emergency information and photograph for your
OFFICIAL PHOTOGRAPHY STAFF identification pass.

SECURITY NOTICE

• This HTTPS link is unique to you. Do not forward or share it.
• It expires after 14 days.
• It becomes unusable immediately after successful submission.
• Sophie’s Photography will never ask you to reply to an email
  with medical information, allergy information or
  emergency-contact details.
• Enter confidential information only through the secure profile page.
• Health, allergy and support information is optional and is used
  only for staff welfare and emergency purposes.
• Confidential information is not displayed on your staff ID pass
  or ordinary event timesheets.

Complete My Staff Profile:
{secure_link}

Staff ID: {staff_code}
Link expires: {expires_text}

If you think this email or link has been shared, do not use it.
Contact Sophie’s Photography so it can be revoked and replaced.

Sophie’s Photography
OFFICIAL PHOTOGRAPHY STAFF
"""

    message = EmailMessage()

    message["From"] = (
        f"{settings.smtp_from_name} "
        f"<{settings.smtp_from_email}>"
    )

    message["To"] = to

    message["Subject"] = (
        "Complete your Sophie’s Photography staff profile"
    )

    message.set_content(plain_text)

    message.add_alternative(
        _staff_email_html(
            staff_name=staff_name,
            staff_code=staff_code,
            secure_link=secure_link,
            expires_text=expires_text,
        ),
        subtype="html",
    )
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


# ============================================================
# CREATE / REISSUE REMOTE REQUEST
# ============================================================

def create_remote_request(
    session,
    settings,
    *,
    source_ref: str,
    staff_code: str,
    staff_name: str,
    preferred_name: str,
    role: str,
    staff_email: str,
) -> dict:

    source_ref = str(source_ref or "").strip()
    email = str(staff_email or "").strip().lower()

    if not source_ref:
        raise HTTPException(
            status_code=400,
            detail="Staff profile source reference is required.",
        )

    if not email or "@" not in email:
        raise HTTPException(
            status_code=400,
            detail="A valid staff email address is required.",
        )

    base_url = str(
        settings.public_base_url or ""
    ).rstrip("/")

    if not base_url.lower().startswith("https://"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Secure staff profile email refused because "
                "the public Cloud URL is not HTTPS."
            ),
        )

    existing = session.scalar(
        select(StaffProfileRequest).where(
            StaffProfileRequest.source_ref
            == source_ref
        )
    )

    # Reissuing a request clears the temporary onboarding
    # submission. Never delete the photograph if that same
    # private object has already become the permanent Staff
    # Master photograph.
    if existing and existing.photo_storage_path:

        permanent_member = session.scalar(
            select(StaffMember).where(
                StaffMember.staff_source_ref
                == source_ref
            )
        )

        permanent_location = (
            str(
                permanent_member.photo_storage_path
                or ""
            ).strip()
            if permanent_member
            else ""
        )

        temporary_location = str(
            existing.photo_storage_path or ""
        ).strip()

        if (
            temporary_location
            and temporary_location
            != permanent_location
        ):
            delete(
                settings,
                temporary_location,
            )

    token = secrets.token_urlsafe(48)

    now = _now()
    expires = now + timedelta(days=14)

    if existing is None:

        row = StaffProfileRequest(
            source_ref=source_ref,
            staff_code=str(staff_code or "")[:80],
            staff_name=str(staff_name or "")[:200],
            preferred_name=str(
                preferred_name or ""
            )[:200],
            role=str(role or "")[:160],
            staff_email=email[:320],
            token_hash=_hash_token(token),
            status="sent",
            expires_at=expires,
        )

        session.add(row)

    else:

        row = existing

        row.staff_code = str(
            staff_code or ""
        )[:80]

        row.staff_name = str(
            staff_name or ""
        )[:200]

        row.preferred_name = str(
            preferred_name or ""
        )[:200]

        row.role = str(
            role or ""
        )[:160]

        row.staff_email = email[:320]

        row.token_hash = _hash_token(token)
        row.status = "sent"

        row.expires_at = expires
        row.emailed_at = None
        row.submitted_at = None

        row.payload_json = ""

        row.photo_storage_path = ""
        row.photo_filename = ""
        row.photo_content_type = ""

        row.updated_at = now

    session.commit()
    session.refresh(row)

    secure_link = (
        f"{base_url}/profile-request/{token}"
    )

    display_name = (
        row.preferred_name.strip()
        or row.staff_name.strip()
        or "Staff Member"
    )

    sent = _send_staff_profile_email(
        settings,
        to=row.staff_email,
        staff_name=display_name,
        staff_code=(
            row.staff_code
            or "Pending"
        ),
        secure_link=secure_link,
        expires=expires,
    )

    if not sent:

        # Never leave an active bearer token behind after
        # a failed email attempt.
        row.status = "email_failed"
        row.token_hash = None

        session.commit()

        raise HTTPException(
            status_code=503,
            detail=(
                "The secure staff profile email could not "
                "be sent. Check Cloud email settings."
            ),
        )

    row.emailed_at = now

    session.commit()

    return {
        "ok": True,
        "source_ref": row.source_ref,
        "status": row.status,
        "emailed": True,
        "emailed_at": (
            row.emailed_at.isoformat()
            if row.emailed_at
            else None
        ),
        "expires_at": (
            row.expires_at.isoformat()
        ),
    }




def purge_remote_request(
    session,
    settings,
    row: StaffProfileRequest,
) -> None:
    """Delete temporary staff profile data and private photo."""

    if row.photo_storage_path:
        delete(settings, row.photo_storage_path)

    session.delete(row)
    session.commit()


# ============================================================
# PUBLIC HTTPS STAFF PROFILE ROUTER
# ============================================================

def build_profile_request_router(
    templates,
) -> APIRouter:

    router = APIRouter(
        tags=["profile-request"]
    )


    @router.get(
        "/profile-request/{token}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def profile_request_page(
        request: Request,
        token: str,
    ):

        with (
            request.app.state.session_factory()
            as session
        ):

            row = _find_live_request(
                session,
                token,
            )

            if row is None:

                return _secure_template(
                    templates,
                    request,
                    "staff_profile_request_closed.html",
                    {},
                    status_code=410,
                )

            member = {
                "staff_code": row.staff_code,
                "name": row.staff_name,
                "preferred_name": row.preferred_name,
                "role": row.role,
                "email": row.staff_email,
            }

        return _secure_template(
            templates,
            request,
            "staff_profile_request.html",
            {
                "member": member,
                "token": token,
            },
        )


    @router.post(
        "/profile-request/{token}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def profile_request_submit(
        request: Request,
        token: str,

        preferred_name: str = Form(""),
        email: str = Form(""),
        telephone: str = Form(""),

        address_line_1: str = Form(""),
        address_line_2: str = Form(""),
        town_city: str = Form(""),
        postcode: str = Form(""),

        date_of_birth: str = Form(""),

        emergency_name: str = Form(""),

        emergency_relationship: str = Form(""),

        emergency_phone: str = Form(""),

        secondary_emergency_name: str = Form(""),

        secondary_emergency_phone: str = Form(""),

        allergies: str = Form(""),

        medical_notes: str = Form(""),

        accessibility_requirements: str = Form(""),

        dietary_requirements: str = Form(""),

        driving_status: str = Form(""),

        uniform_size: str = Form(""),

        photo: UploadFile | None = File(
            default=None
        ),
    ):

        settings = request.app.state.settings

        with (
            request.app.state.session_factory()
            as session
        ):

            row = _find_live_request(
                session,
                token,
            )

            if row is None:

                return _secure_template(
                    templates,
                    request,
                    "staff_profile_request_closed.html",
                    {},
                    status_code=410,
                )


            if not photo or not photo.filename:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Please upload a photograph "
                        "for your staff ID pass."
                    ),
                )


            filename = Path(
                photo.filename
            ).name

            suffix = Path(
                filename
            ).suffix.lower()


            if suffix not in {
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                ".heic",
                ".heif",
            }:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Please upload a JPG, PNG, WebP, "
                        "HEIC or HEIF photograph."
                    ),
                )


            content = await photo.read(
                12 * 1024 * 1024 + 1
            )


            if len(content) > (
                12 * 1024 * 1024
            ):

                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Photograph must be "
                        "smaller than 12 MB."
                    ),
                )


            try:
                content, filename, content_type = (
                    normalise_staff_photo(
                        content,
                        filename,
                    )
                )
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "The photograph could not be read. "
                        "Please choose another JPG, PNG, "
                        "WebP, HEIC or HEIF image."
                    ),
                )


            suffix = ".jpg"


            object_key = (
                "staff-profile-requests/"
                f"{row.id}/"
                f"{secrets.token_hex(12)}"
                f"{suffix}"
            )


            location = upload_bytes(
                settings,
                object_key,
                content,
                content_type,
            )


            payload = {

                "preferred_name":
                    preferred_name.strip()[:160],

                "email":
                    email.strip().lower()[:240],

                "telephone":
                    telephone.strip()[:80],

                "address_line_1":
                    address_line_1.strip()[:240],

                "address_line_2":
                    address_line_2.strip()[:240],

                "town_city":
                    town_city.strip()[:160],

                "postcode":
                    postcode.strip()[:40],

                "date_of_birth":
                    date_of_birth.strip()[:20],

                "emergency_name":
                    emergency_name.strip()[:160],

                "emergency_relationship":
                    emergency_relationship.strip()[:120],

                "emergency_phone":
                    emergency_phone.strip()[:80],

                "secondary_emergency_name":
                    secondary_emergency_name.strip()[:160],

                "secondary_emergency_phone":
                    secondary_emergency_phone.strip()[:80],

                "allergies":
                    allergies.strip()[:2000],

                "medical_notes":
                    medical_notes.strip()[:3000],

                "accessibility_requirements":
                    accessibility_requirements.strip()[:2000],

                "dietary_requirements":
                    dietary_requirements.strip()[:1500],

                "driving_status":
                    driving_status.strip()[:80],

                "uniform_size":
                    uniform_size.strip()[:80],
            }


            now = _now()

            try:

                row.payload_json = json.dumps(
                    payload,
                    separators=(",", ":"),
                )

                row.photo_storage_path = (
                    location
                )

                row.photo_filename = (
                    filename[:240]
                )

                row.photo_content_type = (
                    content_type[:120]
                )

                row.submitted_at = now
                row.status = "submitted"

                # Keep the permanent identity fields aligned with
                # the values confirmed by the staff member.
                row.preferred_name = payload["preferred_name"]
                row.staff_email = payload["email"]

                # Create/update the permanent Staff Master identity.
                # Payroll and availability remain independent and
                # continue to use the shared staff_source_ref.
                _sync_staff_master_from_profile(
                    session,
                    row,
                )

                # Single use: kill the public credential
                # immediately after successful submission.
                row.token_hash = None

                row.updated_at = now

                session.commit()

            except Exception:

                delete(
                    settings,
                    location,
                )

                raise


            display_name = (
                payload["preferred_name"]
                or row.staff_name
                or "Staff Member"
            )


        return _secure_template(
            templates,
            request,
            "staff_profile_request_complete.html",
            {
                "display_name":
                    display_name,
            },
        )


    return router
