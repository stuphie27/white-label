from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.customer_ownership import verified_customer_profile

from app.db.models import (
    CustomerFavourite,
    CustomerFavouriteSession,
    CustomerGalleryActivity,
    CustomerIdentity,
    Event,
)


ACTIVE_MINUTES = 10


def basket_photo_count(request, gallery_id: str) -> int:
    basket = request.session.get(
        f"customer_basket_{gallery_id}",
        [],
    )

    if not isinstance(basket, list):
        return 0

    return len({
        str(item.get("asset_id") or "")
        for item in basket
        if isinstance(item, dict)
        and str(item.get("asset_id") or "")
        and item.get("product_type") not in {
            "favourites_extension",
            "favourites_extension_70",
        }
    })


def favourite_count(db, gallery_id: str, email: str) -> int:
    favourite_session = db.scalar(
        select(CustomerFavouriteSession).where(
            CustomerFavouriteSession.gallery_id == gallery_id,
            func.lower(CustomerFavouriteSession.email)
            == email.lower(),
        )
    )

    if favourite_session is None:
        return 0

    return int(
        db.scalar(
            select(func.count(CustomerFavourite.id)).where(
                CustomerFavourite.session_id
                == favourite_session.id
            )
        )
        or 0
    )


def record_customer_activity(
    db,
    request,
    gallery,
    *,
    area: str = "gallery",
    folder: str = "",
    photo_view: bool = False,
    folder_view: bool = False,
):
    profile = verified_customer_profile(
        db,
        request,
    )

    if not profile:
        return None

    identity_id = str(
        profile.get("identity_id") or ""
    ).strip()

    email = str(
        profile.get("email") or ""
    ).strip().lower()

    name = str(
        profile.get("name") or ""
    ).strip()

    if not identity_id or not email:
        return None

    activity = db.scalar(
        select(CustomerGalleryActivity).where(
            CustomerGalleryActivity.gallery_id == gallery.id,
            CustomerGalleryActivity.customer_identity_id
            == identity_id,
        )
    )

    now = datetime.now(timezone.utc)

    if activity is None:
        activity = CustomerGalleryActivity(
            event_id=gallery.event_id,
            gallery_id=gallery.id,
            customer_identity_id=identity_id,
            customer_name=name,
            customer_email=email,
            current_area=area[:40],
            current_folder=folder[:800],
            first_seen_at=now,
            last_seen_at=now,
        )

        db.add(activity)
        db.flush()

    activity.customer_name = name
    activity.customer_email = email
    activity.current_area = area[:40]
    activity.current_folder = folder[:800]
    activity.last_seen_at = now

    if photo_view:
        activity.photo_views = int(activity.photo_views or 0) + 1

    if folder_view:
        activity.folder_views = int(activity.folder_views or 0) + 1

    activity.favourite_count = favourite_count(
        db,
        gallery.id,
        email,
    )

    activity.basket_photo_count = basket_photo_count(
        request,
        gallery.id,
    )

    db.commit()
    db.refresh(activity)

    return activity


def dashboard_customer_activity(db):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=ACTIVE_MINUTES)

    events = list(
        db.scalars(
            select(Event)
            .where(Event.status == "live")
            .order_by(Event.start_date, Event.name)
        )
    )

    live_rows = list(
        db.scalars(
            select(CustomerGalleryActivity)
            .where(CustomerGalleryActivity.last_seen_at >= cutoff)
            .order_by(CustomerGalleryActivity.last_seen_at.desc())
        )
    )

    online_browsing = len(live_rows)

    online_favourites = sum(
        int(row.favourite_count or 0)
        for row in live_rows
    )

    online_baskets = sum(
        int(row.basket_photo_count or 0)
        for row in live_rows
    )

    offline_browsing = sum(
        int(event.mirrored_customers_browsing or 0)
        for event in events
    )

    offline_favourites = sum(
        int(event.mirrored_active_favourites or 0)
        for event in events
    )

    offline_baskets = sum(
        int(event.mirrored_active_baskets or 0)
        for event in events
    )

    all_rows = list(
        db.scalars(
            select(CustomerGalleryActivity)
            .order_by(CustomerGalleryActivity.last_seen_at.desc())
        )
    )

    return {
        "active_minutes": ACTIVE_MINUTES,

        "offline_browsing": offline_browsing,
        "online_browsing": online_browsing,
        "combined_browsing": (
            offline_browsing + online_browsing
        ),

        "offline_favourites": offline_favourites,
        "online_favourites": online_favourites,
        "combined_favourites": (
            offline_favourites + online_favourites
        ),

        "offline_baskets": offline_baskets,
        "online_baskets": online_baskets,
        "combined_baskets": (
            offline_baskets + online_baskets
        ),

        "online_photo_views": sum(
            int(row.photo_views or 0)
            for row in all_rows
        ),

        "online_folder_views": sum(
            int(row.folder_views or 0)
            for row in all_rows
        ),

        "live_customers": live_rows,
    }
