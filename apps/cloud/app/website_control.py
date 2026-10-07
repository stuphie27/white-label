from __future__ import annotations

import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, inspect, select, text, update

from app.branding import BRANDS
from app.db.models import (
    CloudOrder,
    CustomerDelivery,
    CustomerFavourite,
    CustomerFavouriteSession,
    CustomerGalleryVisit,
    Event,
    Gallery,
    GalleryAsset,
    GalleryFolder,
    StaffMember,
    StaffPasswordReset,
)

LOGGER = logging.getLogger("stuphie.website_control")

CONTROL_EMAIL = "photos@sophiesphotography.co.uk"
MAGIC_LINK_TTL_MINUTES = 10

DAY_NAMES = (
    "Saturday",
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
)

DAY_PREFIXES = {
    "Saturday": "01 - Saturday",
    "Sunday": "02 - Sunday",
    "Monday": "03 - Monday",
    "Tuesday": "04 - Tuesday",
    "Wednesday": "05 - Wednesday",
}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _send_magic_link(request: Request, email: str, token: str) -> None:
    settings = request.app.state.settings

    if not settings.smtp_host:
        raise RuntimeError("SMTP is not configured.")

    login_url = (
        str(request.base_url).rstrip("/")
        + "/stuphie-control/magic/"
        + token
    )

    message = EmailMessage()
    message["From"] = (
        f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    )
    message["To"] = email
    message["Subject"] = "Your White Label Control Centre login"
    message.set_content(
        "White Label Control Centre\n\n"
        "A secure administrator login link was requested.\n\n"
        f"{login_url}\n\n"
        "The link expires in 10 minutes and can only be used once."
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


def _admin_check(request: Request):
    if not request.session.get("staff_authenticated"):
        return RedirectResponse(
            "/stuphie-control/login",
            status_code=303,
        )

    email = str(
        request.session.get("staff_email", "")
    ).strip().lower()

    role = str(
        request.session.get("staff_role", "")
    ).strip().lower()

    if email != CONTROL_EMAIL or role != "super_admin":
        request.session.clear()
        return RedirectResponse(
            "/stuphie-control/login",
            status_code=303,
        )

    request.session["staff_last_activity"] = datetime.now(
        timezone.utc
    ).timestamp()

    return None


def _columns(engine, table: str) -> set[str]:
    try:
        return {
            str(row["name"])
            for row in inspect(engine).get_columns(table)
        }
    except Exception:
        return set()


def _tables(engine) -> set[str]:
    try:
        return set(inspect(engine).get_table_names())
    except Exception:
        return set()


def _row_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row._mapping)
    except Exception:
        return {}


def _event_rows(session, brand_id: str = ""):
    query = select(Event)

    if brand_id:
        query = query.where(Event.brand_id == brand_id)

    return list(
        session.scalars(
            query.order_by(
                Event.start_date.desc(),
                Event.name.asc(),
            )
        )
    )


def _event(session, event_id: str):
    if not event_id:
        return None

    return session.scalar(
        select(Event).where(Event.id == event_id)
    )


def _gallery(session, event_id: str):
    return session.scalar(
        select(Gallery).where(
            Gallery.event_id == event_id
        )
    )


def _photo_counts(session, gallery_id: str):
    """
    Read the production gallery_assets table directly.

    The production schema is PostgreSQL and the live database is the
    source of truth for the White Label dashboard.
    """

    rows = session.execute(
        text("""
            SELECT
                media_kind,
                status,
                COUNT(*) AS amount
            FROM gallery_assets
            WHERE gallery_id = :gallery_id
            GROUP BY media_kind, status
        """),
        {"gallery_id": gallery_id},
    ).all()

    result = {
        "total": 0,
        "ready": 0,
        "waiting": 0,
        "failed": 0,
        "removed": 0,
    }

    for media_kind, status, amount in rows:
        if str(media_kind or "").lower() != "photos":
            continue

        status = str(status or "").lower()
        amount = int(amount or 0)

        if status != "removed":
            result["total"] += amount

        if status == "ready":
            result["ready"] += amount
        elif status in {
            "waiting",
            "pending",
            "processing",
            "uploading",
        }:
            result["waiting"] += amount
        elif status in {"failed", "error"}:
            result["failed"] += amount
        elif status == "removed":
            result["removed"] += amount

    return result


def _day_counts(session, gallery_id: str):
    """
    Calculate event-day statistics directly from gallery_assets.folder_path.

    The International's real production structure uses:
      01 - Saturday/...
      02 - Sunday/...
      etc.

    Only photograph assets are counted.
    """

    result = {
        day: {
            "total": 0,
            "ready": 0,
            "waiting": 0,
            "failed": 0,
            "removed": 0,
            "percent": 0,
        }
        for day in DAY_NAMES
    }

    rows = session.execute(
        text("""
            SELECT
                folder_path,
                status,
                COUNT(*) AS amount
            FROM gallery_assets
            WHERE gallery_id = :gallery_id
              AND lower(media_kind) = 'photos'
            GROUP BY folder_path, status
        """),
        {"gallery_id": gallery_id},
    ).all()

    for folder_path, status, amount in rows:
        path = str(folder_path or "")
        status = str(status or "").lower()
        amount = int(amount or 0)

        day = None

        for name, prefix in DAY_PREFIXES.items():
            if (
                path == prefix
                or path.startswith(prefix + "/")
            ):
                day = name
                break

        if day is None:
            continue

        bucket = result[day]

        if status != "removed":
            bucket["total"] += amount

        if status == "ready":
            bucket["ready"] += amount
        elif status in {
            "waiting",
            "pending",
            "processing",
            "uploading",
        }:
            bucket["waiting"] += amount
        elif status in {"failed", "error"}:
            bucket["failed"] += amount
        elif status == "removed":
            bucket["removed"] += amount

    for bucket in result.values():
        if bucket["total"]:
            bucket["percent"] = round(
                bucket["ready"]
                / bucket["total"]
                * 100,
                1,
            )

    return result


def _folder_counts(session, gallery_id: str):
    """
    Build the live folder inventory from the actual production
    gallery_folders and gallery_assets tables.

    Active folders are the navigation structure. Asset counts come
    from gallery_assets.folder_path, so the displayed count is the
    number of real photographs beneath each folder.
    """

    folder_rows = session.execute(
        text("""
            SELECT
                id,
                name,
                path,
                parent_path,
                media_kind,
                active,
                sort_order
            FROM gallery_folders
            WHERE gallery_id = :gallery_id
              AND lower(media_kind) = 'photos'
              AND active = TRUE
            ORDER BY sort_order, path
        """),
        {"gallery_id": gallery_id},
    ).mappings().all()

    asset_rows = session.execute(
        text("""
            SELECT
                folder_path,
                COUNT(*) AS amount
            FROM gallery_assets
            WHERE gallery_id = :gallery_id
              AND lower(media_kind) = 'photos'
              AND status = 'ready'
            GROUP BY folder_path
        """),
        {"gallery_id": gallery_id},
    ).all()

    asset_counts = {
        str(path or ""): int(amount or 0)
        for path, amount in asset_rows
    }

    result = []

    for folder in folder_rows:
        folder_path = str(folder["path"] or "")
        total = 0

        for asset_path, amount in asset_counts.items():
            if (
                asset_path == folder_path
                or asset_path.startswith(
                    folder_path + "/"
                )
            ):
                total += amount

        result.append(
            {
                "path": folder_path,
                "name": str(
                    folder["name"]
                    or folder_path
                ),
                "parent_path": str(
                    folder["parent_path"]
                    or ""
                ),
                "count": total,
                "active": bool(folder["active"]),
                "sort_order": int(
                    folder["sort_order"] or 0
                ),
            }
        )

    return result

def _favourites(session, gallery_id: str):
    sessions = int(
        session.scalar(
            select(func.count(CustomerFavouriteSession.id)).where(
                CustomerFavouriteSession.gallery_id == gallery_id
            )
        )
        or 0
    )

    favourites = int(
        session.scalar(
            select(func.count(CustomerFavourite.id))
            .join(
                CustomerFavouriteSession,
                CustomerFavouriteSession.id
                == CustomerFavourite.session_id,
            )
            .where(
                CustomerFavouriteSession.gallery_id
                == gallery_id
            )
        )
        or 0
    )

    return {
        "sessions": sessions,
        "favourites": favourites,
    }


def _orders(session, event_id: str):
    total = int(
        session.scalar(
            select(func.count(CloudOrder.id)).where(
                CloudOrder.event_id == event_id
            )
        )
        or 0
    )

    paid = int(
        session.scalar(
            select(func.count(CloudOrder.id)).where(
                CloudOrder.event_id == event_id,
                CloudOrder.payment_status == "paid",
            )
        )
        or 0
    )

    awaiting = total - paid

    revenue_pence = int(
        session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        CloudOrder.payment_amount_pence
                    ),
                    0,
                )
            ).where(
                CloudOrder.event_id == event_id,
                CloudOrder.payment_status == "paid",
            )
        )
        or 0
    )

    rows = list(
        session.scalars(
            select(CloudOrder).where(
                CloudOrder.event_id == event_id
            ).order_by(
                CloudOrder.created_at.desc()
            ).limit(100)
        )
    )

    return {
        "total": total,
        "paid": paid,
        "awaiting": awaiting,
        "revenue": revenue_pence / 100,
        "rows": rows,
    }


def _deliveries(session, event_name: str):
    counts = {
        "waiting": 0,
        "processing": 0,
        "complete": 0,
        "failed": 0,
    }

    rows = list(
        session.scalars(
            select(CustomerDelivery).where(
                CustomerDelivery.event_name == event_name
            ).order_by(
                CustomerDelivery.created_at.desc()
            ).limit(100)
        )
    )

    for row in rows:
        status = str(row.status or "").lower()

        if status in {
            "ready",
            "pending",
            "awaiting_download",
        }:
            counts["waiting"] += 1
        elif status == "processing":
            counts["processing"] += 1
        elif status in {
            "completed",
            "downloaded",
        }:
            counts["complete"] += 1
        elif status in {
            "failed",
            "delivery_failed",
        }:
            counts["failed"] += 1

    counts["rows"] = rows
    return counts


def _activity(session, gallery_id: str):
    engine = session.get_bind()
    table = "customer_gallery_activity"

    if table not in _tables(engine):
        return {
            "events": 0,
            "visitors": 0,
            "gallery_views": 0,
            "photo_views": 0,
            "favourites": 0,
            "basket_activity": 0,
            "checkout_activity": 0,
            "recent": [],
            "available": False,
        }

    columns = _columns(engine, table)

    if "gallery_id" not in columns:
        return {
            "events": 0,
            "visitors": 0,
            "gallery_views": 0,
            "photo_views": 0,
            "favourites": 0,
            "basket_activity": 0,
            "checkout_activity": 0,
            "recent": [],
            "available": False,
        }

    qtable = '"customer_gallery_activity"'

    events = session.execute(
        text(
            f"SELECT COUNT(*) FROM {qtable} "
            "WHERE gallery_id = :gid"
        ),
        {"gid": gallery_id},
    ).scalar() or 0

    visitor_col = next(
        (
            name
            for name in (
                "visitor_id",
                "session_id",
                "customer_session_id",
                "session_token",
            )
            if name in columns
        ),
        None,
    )

    if visitor_col:
        visitors = session.execute(
            text(
                f'SELECT COUNT(DISTINCT "{visitor_col}") '
                f"FROM {qtable} "
                'WHERE gallery_id = :gid'
            ),
            {"gid": gallery_id},
        ).scalar() or 0
    else:
        visitors = events

    action_col = next(
        (
            name
            for name in (
                "activity_type",
                "event_type",
                "action",
                "activity",
            )
            if name in columns
        ),
        None,
    )

    gallery_views = 0
    photo_views = 0
    favourite_events = 0
    basket_events = 0
    checkout_events = 0

    if action_col:
        rows = session.execute(
            text(
                f'SELECT "{action_col}", COUNT(*) '
                f'FROM {qtable} '
                'WHERE gallery_id = :gid '
                f'GROUP BY "{action_col}"'
            ),
            {"gid": gallery_id},
        ).all()

        for action, amount in rows:
            label = str(action or "").lower()
            amount = int(amount or 0)

            if "gallery" in label and (
                "view" in label or "open" in label
            ):
                gallery_views += amount

            if "photo" in label and (
                "view" in label or "open" in label
            ):
                photo_views += amount

            if "favour" in label:
                favourite_events += amount

            if "basket" in label or "cart" in label:
                basket_events += amount

            if "checkout" in label:
                checkout_events += amount

    timestamp_col = next(
        (
            name
            for name in (
                "created_at",
                "occurred_at",
                "recorded_at",
                "timestamp",
            )
            if name in columns
        ),
        None,
    )

    recent = []

    if timestamp_col:
        selectable = [
            name
            for name in (
                action_col,
                "page",
                "path",
                "folder_path",
                "photo_reference",
                "photo_id",
            )
            if name and name in columns
        ]

        fields = [
            f'"{name}"'
            for name in selectable
        ]
        fields.append(f'"{timestamp_col}"')

        result = session.execute(
            text(
                f'SELECT {", ".join(fields)} '
                f'FROM {qtable} '
                'WHERE gallery_id = :gid '
                f'ORDER BY "{timestamp_col}" DESC '
                'LIMIT 15'
            ),
            {"gid": gallery_id},
        ).all()

        for row in result:
            values = list(row)
            item = {}

            for index, name in enumerate(selectable):
                item[name] = values[index]

            stamp = values[-1]

            item["time"] = (
                stamp.strftime("%d %b %H:%M")
                if hasattr(stamp, "strftime")
                else str(stamp or "")
            )

            recent.append(item)

    anonymous_visitors = session.execute(
        text("""
            SELECT COUNT(DISTINCT visitor_id)
            FROM customer_gallery_visits
            WHERE gallery_id = :gid
        """),
        {"gid": gallery_id},
    ).scalar() or 0

    anonymous_gallery_views = session.execute(
        text("""
            SELECT COALESCE(SUM(gallery_views), 0)
            FROM customer_gallery_visits
            WHERE gallery_id = :gid
        """),
        {"gid": gallery_id},
    ).scalar() or 0

    anonymous_photo_views = session.execute(
        text("""
            SELECT COALESCE(SUM(photo_views), 0)
            FROM customer_gallery_visits
            WHERE gallery_id = :gid
        """),
        {"gid": gallery_id},
    ).scalar() or 0

    return {
        "events": int(events),
        "visitors": int(visitors) + int(anonymous_visitors),
        "gallery_views": int(gallery_views) + int(anonymous_gallery_views),
        "photo_views": int(photo_views) + int(anonymous_photo_views),
        "favourites": favourite_events,
        "basket_activity": basket_events,
        "checkout_activity": checkout_events,
        "recent": recent,
        "available": True,
    }


def _basket_stats(session, gallery_id: str):
    engine = session.get_bind()
    tables = _tables(engine)

    candidates = [
        table
        for table in tables
        if "basket" in table.lower()
        or "cart" in table.lower()
    ]

    for table in candidates:
        columns = _columns(engine, table)

        gallery_col = next(
            (
                name
                for name in (
                    "gallery_id",
                    "event_id",
                )
                if name in columns
            ),
            None,
        )

        if not gallery_col:
            continue

        try:
            value = session.execute(
                text(
                    f'SELECT COUNT(*) FROM "{table}" '
                    f'WHERE "{gallery_col}" = :gid'
                ),
                {"gid": gallery_id},
            ).scalar()

            return {
                "count": int(value or 0),
                "tracked": True,
            }
        except Exception:
            continue

    return {
        "count": 0,
        "tracked": False,
    }


def _system_health(session, gallery):
    engine = session.get_bind()

    health = []

    try:
        session.execute(text("SELECT 1"))
        health.append(
            {
                "name": "Database",
                "status": "Healthy",
                "ok": True,
            }
        )
    except Exception:
        health.append(
            {
                "name": "Database",
                "status": "Unavailable",
                "ok": False,
            }
        )

    required = {
        "events",
        "galleries",
        "gallery_assets",
        "gallery_folders",
        "customer_deliveries",
    }

    missing = sorted(
        required - _tables(engine)
    )

    health.append(
        {
            "name": "Database schema",
            "status": (
                "Ready" if not missing
                else "Missing tables"
            ),
            "ok": not missing,
        }
    )

    settings = getattr(
        session,
        "bind",
        None,
    )

    health.append(
        {
            "name": "Gallery",
            "status": (
                "Connected" if gallery is not None
                else "Not configured"
            ),
            "ok": gallery is not None,
        }
    )

    return health


def _context(session, event_id: str):
    event = _event(session, event_id)

    if event is None:
        return None

    gallery = _gallery(
        session,
        event.id,
    )

    photos = {
        "total": 0,
        "ready": 0,
        "waiting": 0,
        "failed": 0,
        "removed": 0,
    }

    days = {
        day: {
            "total": 0,
            "ready": 0,
            "waiting": 0,
            "failed": 0,
            "removed": 0,
            "percent": 0,
        }
        for day in DAY_NAMES
    }

    folders = []
    favourites = {
        "sessions": 0,
        "favourites": 0,
    }
    activity = {
        "events": 0,
        "visitors": 0,
        "gallery_views": 0,
        "photo_views": 0,
        "favourites": 0,
        "basket_activity": 0,
        "checkout_activity": 0,
        "recent": [],
        "available": False,
    }
    baskets = {
        "count": 0,
        "tracked": False,
    }

    if gallery is not None:
        photos = _photo_counts(
            session,
            gallery.id,
        )

        days = _day_counts(
            session,
            gallery.id,
        )

        folders = _folder_counts(
            session,
            gallery.id,
        )

        favourites = _favourites(
            session,
            gallery.id,
        )

        activity = _activity(
            session,
            gallery.id,
        )

        baskets = _basket_stats(
            session,
            gallery.id,
        )

    orders = _orders(
        session,
        event.id,
    )

    deliveries = _deliveries(
        session,
        event.name,
    )

    health = _system_health(
        session,
        gallery,
    )

    return {
        "event": event,
        "gallery": gallery,
        "photos": photos,
        "days": days,
        "folders": folders,
        "favourites": favourites,
        "activity": activity,
        "baskets": baskets,
        "orders": orders,
        "deliveries": deliveries,
        "health": health,
    }


def _brand_name(brand_id: str):
    brand = BRANDS.get(
        brand_id,
        {},
    )
    return brand.get(
        "display_name",
        brand_id,
    )


def _brand_host(brand_id: str):
    brand = BRANDS.get(
        brand_id,
        {},
    )
    return str(
        brand.get("public_host", "")
    ).strip()


def build_website_control_router(
    templates: Jinja2Templates,
) -> APIRouter:

    router = APIRouter(
        prefix="/stuphie-control",
        tags=["website-control"],
    )

    @router.get(
        "/login",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def login_page(
        request: Request,
        message: str = "",
        error: str = "",
    ):
        if (
            request.session.get("staff_email", "")
            .strip()
            .lower()
            == CONTROL_EMAIL
            and request.session.get("staff_role", "")
            .strip()
            .lower()
            == "super_admin"
        ):
            return RedirectResponse(
                "/stuphie-control",
                status_code=303,
            )

        return templates.TemplateResponse(
            request=request,
            name="website_control_login.html",
            context={
                "message": message,
                "error": error,
            },
        )

    @router.post(
        "/login",
        include_in_schema=False,
    )
    async def login_submit(
        request: Request,
        email: str = Form(...),
    ):
        if email.strip().lower() != CONTROL_EMAIL:
            return RedirectResponse(
                "/stuphie-control/login?message="
                "If that address is authorised, "
                "a secure login link has been sent.",
                status_code=303,
            )

        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if factory is None:
            return RedirectResponse(
                "/stuphie-control/login?error="
                "The control centre database is not available.",
                status_code=303,
            )

        with factory() as session:
            staff = session.scalar(
                select(StaffMember).where(
                    StaffMember.email.ilike(
                        CONTROL_EMAIL
                    ),
                    StaffMember.active.is_(True),
                )
            )

            if staff is None:
                return RedirectResponse(
                    "/stuphie-control/login?error="
                    "The authorised control account is not configured.",
                    status_code=303,
                )

            token = secrets.token_urlsafe(48)
            now = datetime.now(timezone.utc)

            session.execute(
                update(StaffPasswordReset)
                .where(
                    StaffPasswordReset.staff_id == staff.id,
                    StaffPasswordReset.used_at.is_(None),
                )
                .values(used_at=now)
            )

            session.add(
                StaffPasswordReset(
                    staff_id=staff.id,
                    token_digest=_digest(token),
                    expires_at=now + timedelta(
                        minutes=MAGIC_LINK_TTL_MINUTES
                    ),
                )
            )

            session.commit()

        settings = request.app.state.settings

        if settings.environment in {
            "development",
            "test",
        }:
            login_url = (
                str(request.base_url).rstrip("/")
                + "/stuphie-control/magic/"
                + token
            )

            LOGGER.warning(
                "WHITE LABEL LOCAL MAGIC LINK: %s",
                login_url,
            )

            return templates.TemplateResponse(
                request=request,
                name="website_control_login.html",
                context={
                    "message": "Your secure login link is ready.",
                    "error": "",
                    "local_magic_link": login_url,
                },
            )

        try:
            _send_magic_link(
                request,
                CONTROL_EMAIL,
                token,
            )
        except Exception:
            LOGGER.exception(
                "White Label control magic-link email failed"
            )

            return RedirectResponse(
                "/stuphie-control/login?error="
                "The login email could not be sent. "
                "Please try again.",
                status_code=303,
            )

        return RedirectResponse(
            "/stuphie-control/login?message="
            "Check photos@sophiesphotography.co.uk "
            "for your secure login link.",
            status_code=303,
        )

    @router.get(
        "/magic/{token}",
        include_in_schema=False,
    )
    async def magic_login(
        request: Request,
        token: str,
    ):
        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if factory is None:
            return RedirectResponse(
                "/stuphie-control/login?error="
                "The control centre database is not available.",
                status_code=303,
            )

        now = datetime.now(timezone.utc)

        with factory() as session:
            challenge = session.scalar(
                select(StaffPasswordReset).where(
                    StaffPasswordReset.token_digest
                    == _digest(token),
                    StaffPasswordReset.used_at.is_(None),
                    StaffPasswordReset.expires_at > now,
                )
            )

            if challenge is None:
                return RedirectResponse(
                    "/stuphie-control/login?error="
                    "This login link has expired or has already been used.",
                    status_code=303,
                )

            staff = session.scalar(
                select(StaffMember).where(
                    StaffMember.id == challenge.staff_id,
                    StaffMember.email.ilike(
                        CONTROL_EMAIL
                    ),
                    StaffMember.active.is_(True),
                )
            )

            if staff is None:
                return RedirectResponse(
                    "/stuphie-control/login?error="
                    "This login link is not authorised.",
                    status_code=303,
                )

            challenge.used_at = now
            session.commit()

        request.session.clear()
        request.session["staff_authenticated"] = True
        request.session["staff_email"] = CONTROL_EMAIL
        request.session["staff_role"] = "super_admin"
        request.session["staff_id"] = staff.id
        request.session["staff_login_at"] = now.timestamp()
        request.session["staff_last_activity"] = now.timestamp()

        return RedirectResponse(
            "/stuphie-control",
            status_code=303,
        )

    @router.get(
        "/logout",
        include_in_schema=False,
    )
    async def logout(request: Request):
        request.session.clear()

        return RedirectResponse(
            "/stuphie-control/login",
            status_code=303,
        )

    @router.get(
        "",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def dashboard(request: Request):
        denied = _admin_check(request)

        if denied:
            return denied

        factory = request.app.state.session_factory

        brand_id = str(
            request.query_params.get(
                "brand",
                "",
            )
        ).strip()

        event_id = str(
            request.query_params.get(
                "event",
                "",
            )
        ).strip()

        with factory() as session:
            all_events = _event_rows(session)

            available_brand_ids = sorted(
                {
                    str(
                        event.brand_id
                        or "sophies"
                    )
                    for event in all_events
                }
                | set(BRANDS.keys())
            )

            # -------------------------------------------------
            # Event selection is authoritative.
            #
            # If an event ID is supplied, NEVER replace it with
            # the first event for a brand. Derive the brand from
            # the selected event itself.
            # -------------------------------------------------
            if event_id:
                selected = _event(
                    session,
                    event_id,
                )

                if selected is not None:
                    brand_id = str(
                        selected.brand_id
                        or "sophies"
                    )
                    events = _event_rows(
                        session,
                        brand_id,
                    )
                else:
                    event_id = ""

            # -------------------------------------------------
            # No event supplied.
            #
            # Prefer DSI/partner when it has a current production
            # event. This is the live White Label partner control
            # centre and prevents the page opening on an unrelated
            # Sophie event such as Champions Of Tomorrow.
            #
            # If partner has no events, fall back safely to the
            # first available brand/event.
            # -------------------------------------------------
            if not event_id:
                partner_events = _event_rows(
                    session,
                    "partner",
                )

                if partner_events:
                    brand_id = "partner"
                    events = partner_events
                    event_id = str(
                        partner_events[0].id
                    )
                else:
                    if not brand_id:
                        brand_id = "sophies"

                    events = _event_rows(
                        session,
                        brand_id,
                    )

                    if events:
                        event_id = str(
                            events[0].id
                        )

            context = (
                _context(
                    session,
                    event_id,
                )
                if event_id
                else None
            )

        return templates.TemplateResponse(
            request=request,
            name="website_control.html",
            context={
                "brands": [
                    {
                        "id": item,
                        "name": _brand_name(item),
                    }
                    for item in available_brand_ids
                ],
                "events": events,
                "brand_id": brand_id,
                "brand_name": _brand_name(brand_id),
                "context": context,
                "public_host": _brand_host(brand_id),
                "staff_email": request.session.get(
                    "staff_email",
                    "",
                ),
                "generated_at": datetime.now(
                    timezone.utc,
                ),
            },
        )

    def _detail_context(
        request: Request,
        event_id: str,
        section: str,
        title: str,
        subtitle: str,
    ):
        denied = _admin_check(request)

        if denied:
            return denied

        with request.app.state.session_factory() as session:
            context = _context(
                session,
                event_id,
            )

        if context is None:
            return templates.TemplateResponse(
                request=request,
                name="website_control_detail.html",
                context={
                    "title": title,
                    "subtitle": subtitle,
                    "section": section,
                    "context": None,
                    "orders": [],
                    "deliveries": [],
                    "selected_day": "",
                    "selected_folder": "",
                },
                status_code=404,
            )

        return templates.TemplateResponse(
            request=request,
            name="website_control_detail.html",
            context={
                "title": title,
                "subtitle": subtitle,
                "section": section,
                "context": context,
                "orders": context["orders"]["rows"],
                "deliveries": context["deliveries"]["rows"],
                "selected_day": "",
                "selected_folder": "",
            },
        )

    @router.get(
        "/photos",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def photos(
        request: Request,
        event: str = "",
    ):
        return _detail_context(
            request,
            event,
            "photos",
            "Photo Pipeline",
            "Complete photograph processing status",
        )

    @router.get(
        "/folders",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def folders(
        request: Request,
        event: str = "",
        day: str = "",
        folder: str = "",
    ):
        denied = _admin_check(request)

        if denied:
            return denied

        with request.app.state.session_factory() as session:
            context = _context(
                session,
                event,
            )

        if context is None:
            return templates.TemplateResponse(
                request=request,
                name="website_control_detail.html",
                context={
                    "title": "Gallery Folders",
                    "subtitle": "Live folder structure and photograph counts",
                    "section": "folders",
                    "context": None,
                    "orders": [],
                    "deliveries": [],
                    "selected_day": day,
                    "selected_folder": folder,
                },
                status_code=404,
            )

        wanted_day = str(day or "").strip().lower()
        wanted_folder = str(folder or "").strip().lower()

        filtered = []

        for row in context["folders"]:
            path = str(row["path"] or "")
            path_lower = path.lower()

            if wanted_folder:
                if (
                    path_lower != wanted_folder
                    and not path_lower.startswith(
                        wanted_folder + "/"
                    )
                ):
                    continue

            if wanted_day:
                prefix = DAY_PREFIXES.get(
                    wanted_day.capitalize(),
                    "",
                ).lower()

                if prefix and not (
                    path_lower == prefix
                    or path_lower.startswith(prefix + "/")
                ):
                    continue

            filtered.append(row)

        context["folders"] = filtered

        return templates.TemplateResponse(
            request=request,
            name="website_control_detail.html",
            context={
                "title": "Gallery Folders",
                "subtitle": "Live folder structure and photograph counts",
                "section": "folders",
                "context": context,
                "orders": context["orders"]["rows"],
                "deliveries": context["deliveries"]["rows"],
                "selected_day": day,
                "selected_folder": folder,
            },
        )

    @router.get(
        "/activity",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def activity(
        request: Request,
        event: str = "",
    ):
        return _detail_context(
            request,
            event,
            "activity",
            "Website Activity",
            "Visitors, gallery views and customer behaviour",
        )
    @router.get(
        "/order/{order_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def order_detail(
        request: Request,
        order_id: str,
        event: str = "",
    ):
        denied = _admin_check(request)

        if denied:
            return denied

        with request.app.state.session_factory() as session:
            order = session.get(CloudOrder, order_id)

            if order is None:
                return HTMLResponse("Order not found", status_code=404)

            # Orders must remain scoped to their own event.
            event_id = str(order.event_id)

            if event and str(event) != event_id:
                return HTMLResponse("Order not found", status_code=404)

            context = _context(session, event_id)

            if context is None:
                return HTMLResponse("Event not found", status_code=404)

            delivery = None

            if order.delivery_id:
                delivery = session.get(
                    CustomerDelivery,
                    order.delivery_id,
                )

            items = []

            try:
                import json

                raw_items = json.loads(
                    order.order_items_json or "[]"
                )

                for item in raw_items:
                    source_ref = str(
                        item.get("asset_source_ref") or ""
                    ).strip()

                    asset = None

                    if source_ref:
                        asset = session.scalar(
                            select(GalleryAsset).where(
                                GalleryAsset.source_ref == source_ref,
                                GalleryAsset.gallery_id == context["gallery"].id,
                            )
                        )

                    items.append(
                        {
                            "source_ref": source_ref,
                            "filename": (
                                item.get("filename")
                                or (
                                    asset.filename
                                    if asset
                                    else ""
                                )
                            ),
                            "folder_path": (
                                item.get("folder_path")
                                or (
                                    asset.folder_path
                                    if asset
                                    else ""
                                )
                            ),
                            "product_name": (
                                item.get("product_name")
                                or order.product_type
                                or ""
                            ),
                            "product_type": (
                                item.get("product_type")
                                or order.product_type
                                or ""
                            ),
                            "quantity": item.get(
                                "quantity",
                                1,
                            ),
                            "unit_price_pence": item.get(
                                "unit_price_pence",
                                0,
                            ),
                        }
                    )

            except (TypeError, ValueError):
                items = []

        return templates.TemplateResponse(
            request=request,
            name="website_control_detail.html",
            context={
                "title": "Order Details",
                "subtitle": (
                    order.order_reference
                    or order.id
                ),
                "section": "order_detail",
                "context": context,
                "orders": [],
                "deliveries": [],
                "selected_day": "",
                "selected_folder": "",
                "order": order,
                "delivery": delivery,
                "order_items": items,
            },
        )
    @router.get(
        "/orders",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def orders(
        request: Request,
        event: str = "",
    ):
        return _detail_context(
            request,
            event,
            "orders",
            "Orders & Revenue",
            "Orders, payment status and event revenue",
        )

    @router.get(
        "/deliveries",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def deliveries(
        request: Request,
        event: str = "",
    ):
        return _detail_context(
            request,
            event,
            "deliveries",
            "Digital Delivery",
            "Customer delivery and download status",
        )

    @router.get(
        "/health",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def health(
        request: Request,
        event: str = "",
    ):
        return _detail_context(
            request,
            event,
            "health",
            "System Health",
            "White Label platform health",
        )

    return router
