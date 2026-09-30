from __future__ import annotations

import hashlib
import os
import secrets
import smtplib
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from shutil import copyfileobj
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CustomerDelivery, CloudOrder, Event
from app.email_design import branded_email_html, attach_brand_logo
from app.branding import get_event_brand
from app.storage import delete as delete_storage, upload_file, spaces_enabled

POLICY_FILES = {
    "low_res": "Low Resolution Image Policy.pdf",
    "high_res": "High Resolution Image Policy.pdf",
    "video": "Video Commercial Usage for Dance.pdf",
}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _safe_name(value: str, fallback: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " .-_" else "-" for c in value).strip(" .-")
    return cleaned[:120] or fallback


def _send(settings, *, to: str, subject: str, body: str, action_label: str = "", action_url: str = "", customer_message: bool = False, cc_delivery_admin: bool = False, brand: dict | None = None) -> bool:
    if not settings.smtp_host:
        return False
    brand = brand or {}

    sender_name = str(
        brand.get("sender_name")
        or brand.get("display_name")
        or settings.smtp_from_name
    )

    sender_email = str(
        brand.get("sender_email")
        or settings.smtp_from_email
    )

    brand_id = str(
        brand.get("brand_id") or ""
    ).strip()

    profile = (
        settings.smtp_profiles.get(brand_id, {})
        if brand_id
        else {}
    )

    if not isinstance(profile, dict):
        profile = {}

    smtp_host = str(
        profile.get("host")
        or settings.smtp_host
    )

    smtp_port = int(
        profile.get("port")
        or settings.smtp_port
    )

    smtp_username = str(
        profile.get("username")
        or settings.smtp_username
    )

    smtp_password = str(
        profile.get("password")
        or settings.smtp_password
    )

    smtp_use_tls = bool(
        profile.get(
            "use_tls",
            settings.smtp_use_tls,
        )
    )

    if not smtp_host:
        return False

    message = EmailMessage()
    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = to
    if cc_delivery_admin and settings.delivery_admin_email:
        admin_email = str(settings.delivery_admin_email).strip()
        if admin_email and admin_email.lower() != str(to).strip().lower():
            message["Cc"] = admin_email
    message["Subject"] = subject
    message.set_content(body)
    message.add_alternative(
        branded_email_html(
            subject, body, action_label=action_label, action_url=action_url,
            customer_email=to,
            include_marketing_controls=customer_message,
            brand=brand,
        ),
        subtype="html",
    )
    attach_brand_logo(message, brand)
    with smtplib.SMTP(
        smtp_host,
        smtp_port,
        timeout=20,
    ) as smtp:
        if smtp_use_tls:
            smtp.starttls()

        if smtp_username:
            smtp.login(
                smtp_username,
                smtp_password,
            )

        smtp.send_message(message)
    return True


def create_delivery(session: Session, settings, *, source_ref: str | None, event_name: str, order_reference: str,
                    customer_name: str, customer_email: str, customer_phone: str, delivery_type: str,
                    files: Iterable[Path], customer_filenames: Iterable[str] | None = None,
                    brand: dict | None = None) -> tuple[CustomerDelivery, str]:
    if delivery_type not in POLICY_FILES:
        raise ValueError("Unsupported delivery type")
    brand = brand or {}

    display_name = str(
        brand.get("display_name")
        or "Sophie’s Photography"
    )

    support_email = str(
        brand.get("sender_email")
        or settings.smtp_from_email
    )

    paths = [Path(p).resolve() for p in files]

    filenames = [
        str(value or "").strip()
        for value in (customer_filenames or [])
    ]

    if not paths or any(not p.is_file() for p in paths):
        raise ValueError("At least one valid delivery file is required")

    if filenames and len(filenames) != len(paths):
        raise ValueError(
            "Customer filenames do not match delivery files"
        )
    token = secrets.token_urlsafe(36)
    now = datetime.now(timezone.utc)
    root = Path(settings.delivery_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    delivery_id = secrets.token_hex(12)
    zip_path = root / f"delivery-{delivery_id}.zip"
    policy_map = brand.get("delivery_policies") or {}
    policy_name = str(
        policy_map.get(delivery_type)
        or POLICY_FILES[delivery_type]
    ).strip()

    policy = (
        Path(__file__).resolve().parents[1]
        / "policies"
        / policy_name
    )

    if not policy.is_file():
        raise FileNotFoundError(
            f"Delivery policy not found: {policy_name}"
        )

    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:

        for index, delivery_path in enumerate(paths):
            customer_filename = (
                filenames[index]
                if filenames
                else delivery_path.name
            )

            # Delivery derivatives are JPEG files.
            # Preserve normal JPG/JPEG customer filenames exactly.
            # For other source formats preserve the original reference
            # but use the correct .jpg extension.
            original = Path(customer_filename)

            if original.suffix.lower() not in {".jpg", ".jpeg"}:
                customer_filename = original.stem + ".jpg"

            archive.write(
                delivery_path,
                arcname=(
                    "Your Photos/"
                    + _safe_name(
                        customer_filename,
                        f"photo-{index + 1}.jpg",
                    )
                ),
            )

        archive.write(
            policy,
            arcname=f"Licence/{policy.name}",
        )
        readme = (
            f"Thank you for your purchase from {display_name}.\n\n"
            "Please read the enclosed licence before using these files.\n"
            f"Copyright remains with {display_name}.\n"
            f"Questions: {support_email}\n"
        )
        archive.writestr("Read Me First.txt", readme)

    if spaces_enabled(settings):
        storage_location = upload_file(
            settings,
            f"deliveries/{delivery_id}.zip",
            zip_path,
            "application/zip",
        )
        zip_path.unlink(missing_ok=True)
    else:
        storage_location = str(zip_path)

    expires = now + timedelta(days=5)
    delivery = CustomerDelivery(source_ref=source_ref or None, event_name=event_name.strip(), order_reference=order_reference.strip(),
        customer_name=customer_name.strip(), customer_email=customer_email.strip().lower(), customer_phone=customer_phone.strip(),
        delivery_type=delivery_type, token_hash=token_hash(token), zip_path=storage_location, item_count=len(paths), status="ready",
        expires_at=expires, reminder_due_at=expires - timedelta(days=1), max_downloads=0)
    session.add(delivery); session.commit(); session.refresh(delivery)
    public_host = str(
        brand.get("public_host") or ""
    ).strip()

    public_base = (
        f"https://{public_host}"
        if public_host
        else settings.public_base_url.rstrip("/")
    )

    link = f"{public_base}/delivery/{token}"
    sent = _send(settings, to=delivery.customer_email, subject=f"Your {display_name} files are ready",
        body=f"Hello {delivery.customer_name},\n\nYour photographs are ready to download securely.\n\nOrder: {delivery.order_reference or 'Your order'}\nEvent: {delivery.event_name}\nFiles: {delivery.item_count}\nDownloads available: Unlimited until expiry\n\nThis personal link expires on {expires:%d %B %Y at %H:%M UTC}. You can download your files as many times as needed during the five-day delivery window.",
        action_label="Download my photographs", action_url=link, customer_message=True,
        cc_delivery_admin=True, brand=brand)
    _send(settings, to=settings.delivery_admin_email, subject=f"Backup delivery link — {delivery.order_reference or delivery.customer_name}",
        body=f"Customer: {delivery.customer_name}\nEvent: {delivery.event_name}\nOrder: {delivery.order_reference}\nFiles: {delivery.item_count}\nBackup link: {link}\nExpires: {expires:%d %B %Y at %H:%M UTC}\n",
        brand=brand)
    if sent:
        delivery.emailed_at = now; delivery.status = "sent"; session.commit()
    return delivery, token


def find_by_token(session: Session, token: str) -> CustomerDelivery | None:
    hashed = token_hash(token)
    return session.scalar(select(CustomerDelivery).where((CustomerDelivery.token_hash == hashed) | (CustomerDelivery.reminder_token_hash == hashed)))


def send_due_reminders(
    session: Session,
    settings,
    now: datetime | None = None,
) -> int:
    """Send one final reminder in the last 24 hours before expiry.

    A reminder is sent only when:
    - the customer has not downloaded the delivery;
    - the delivery is still active;
    - the reminder has never previously been sent; and
    - expiry is no more than 24 hours away.

    Existing deliveries are governed by their actual expires_at value so
    deliveries created under the previous day-3 policy also obey the new rule.
    """
    now = now or datetime.now(timezone.utc)
    final_day = now + timedelta(days=1)

    due = list(
        session.scalars(
            select(CustomerDelivery).where(
                CustomerDelivery.downloaded_at.is_(None),
                CustomerDelivery.deleted_at.is_(None),
                CustomerDelivery.reminder_sent_at.is_(None),
                CustomerDelivery.expires_at > now,
                CustomerDelivery.expires_at <= final_day,
            )
        )
    )

    count = 0

    for d in due:
        reminder_token = secrets.token_urlsafe(36)
        d.reminder_token_hash = token_hash(reminder_token)

        order = None
        event = None

        if d.source_ref:
            order = session.scalar(
                select(CloudOrder).where(
                    CloudOrder.source_ref == d.source_ref
                )
            )

        if order is not None:
            event = session.get(
                Event,
                order.event_id,
            )

        brand = get_event_brand(event)

        public_host = str(
            brand.get("public_host") or ""
        ).strip()

        public_base = (
            f"https://{public_host}"
            if public_host
            else settings.public_base_url.rstrip("/")
        )

        display_name = str(
            brand.get("display_name")
            or "Photography"
        ).strip()

        reminder_link = (
            f"{public_base}"
            f"/delivery/{reminder_token}"
        )

        sent = _send(
            settings,
            to=d.customer_email,
            subject=(
                f"Reminder: your {display_name} "
                "download expires tomorrow"
            ),
            body=(
                f"Hello {d.customer_name},\\n\\n"
                "You have not downloaded your photographs yet. "
                "Your secure delivery expires tomorrow.\\n\\n"
                f"Order: {d.order_reference or 'Your order'}\\n"
                f"Event: {d.event_name}\\n\\n"
                "You can download your photographs as many times "
                "as needed until the secure link expires."
            ),
            action_label="Download my photographs",
            action_url=reminder_link,
            customer_message=True,
            brand=brand,
        )

        if sent:
            d.reminder_sent_at = now
            count += 1

            _send(
                settings,
                to=settings.delivery_admin_email,
                subject=(
                    "Final delivery reminder sent — "
                    f"{d.order_reference or d.customer_name}"
                ),
                body=(
                    f"Customer: {d.customer_name}\\n"
                    f"Event: {d.event_name}\\n"
                    f"Order: {d.order_reference}\\n"
                    "Status: final 24-hour reminder sent; "
                    "customer has not downloaded yet.\\n"
                ),
            )

    session.commit()
    return count


def complete_download(session: Session, settings, delivery_id: str) -> None:
    d = session.get(CustomerDelivery, delivery_id)
    if not d or d.deleted_at or d.status in {"closed", "expired"}:
        return
    now = datetime.now(timezone.utc)
    d.download_count = int(d.download_count or 0) + 1
    if d.downloaded_at is None:
        d.downloaded_at = now
    order = None
    if d.source_ref:
        order = session.scalar(select(CloudOrder).where(CloudOrder.source_ref == d.source_ref))
    if order is not None:
        # The order is fulfilled after the first successful customer download.
        # The private ZIP remains available for unlimited repeat downloads until
        # the five-day expiry time.
        order.status = "completed"
        order.completed_at = order.completed_at or now
        order.fulfilment_error = ""
    d.status = "downloaded"
    session.commit()
    _send(
        settings,
        to=settings.delivery_admin_email,
        subject=f"Customer download — {d.order_reference or d.customer_name}",
        body=(
            f"Customer: {d.customer_name}\nEvent: {d.event_name}\n"
            f"Order: {d.order_reference}\nDelivery: {d.delivery_type}\n"
            f"Downloaded: {now:%d %B %Y at %H:%M UTC}\nFiles: {d.item_count}\n"
            f"Download count: {d.download_count}\nDownloads remaining: Unlimited until expiry\n"
            f"Secure delivery expires: {d.expires_at:%d %B %Y at %H:%M UTC}\n"
        ),
    )



def revoke_delivery(
    session: Session,
    settings,
    delivery_id: str,
) -> bool:
    """Immediately revoke one secure customer delivery.

    Revocation invalidates every bearer link for the delivery because the
    public routes reject closed/deleted deliveries. The stored ZIP is also
    removed where possible.

    This does not alter Offline/event collection state.
    """

    d = session.get(CustomerDelivery, delivery_id)

    if not d:
        return False

    if d.deleted_at or d.status in {"closed", "expired"}:
        return True

    now = datetime.now(timezone.utc)

    try:
        delete_storage(settings, str(d.zip_path or ""))
    except Exception:
        if d.zip_path and not str(d.zip_path).startswith("spaces://"):
            Path(d.zip_path).unlink(missing_ok=True)

    d.status = "closed"
    d.deleted_at = now

    # Revoke both original and reminder bearer credentials immediately.
    d.token_hash = hashlib.sha256(
        secrets.token_urlsafe(48).encode("utf-8")
    ).hexdigest()

    d.reminder_token_hash = None

    session.commit()

    return True


def expire_due(
    session: Session,
    settings,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)

    due = list(
        session.scalars(
            select(CustomerDelivery).where(
                CustomerDelivery.deleted_at.is_(None),
                CustomerDelivery.expires_at <= now,
            )
        )
    )

    for d in due:
        try:
            delete_storage(settings, str(d.zip_path or ""))
        except Exception:
            # Expiry must still close the delivery record if the physical
            # object has already disappeared.
            if d.zip_path and not str(d.zip_path).startswith("spaces://"):
                Path(d.zip_path).unlink(missing_ok=True)

        d.deleted_at = now
        d.status = "expired"

    session.commit()
    return len(due)
