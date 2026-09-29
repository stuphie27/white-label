from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CloudOrder, CustomerDelivery, CustomerGalleryActivity, Event, OrderAudit, TransferJob


def record_order_audit(
    session: Session,
    order: CloudOrder | None,
    event_type: str,
    *,
    actor: str = "system",
    system: str = "cloud",
    old_status: str = "",
    new_status: str = "",
    detail: str = "",
) -> OrderAudit:
    audit = OrderAudit(
        order_id=order.id if order else None,
        order_reference=(order.order_reference or order.source_ref or order.id) if order else "",
        event_type=event_type[:80],
        actor=actor[:120],
        system=system[:40],
        old_status=(old_status or "")[:50],
        new_status=(new_status or "")[:50],
        detail=(detail or "")[:2000],
    )
    session.add(audit)
    return audit


def operations_summary(session: Session) -> dict[str, object]:
    order_statuses = dict(session.execute(select(CloudOrder.status, func.count()).group_by(CloudOrder.status)).all())
    payment_statuses = dict(session.execute(select(CloudOrder.payment_status, func.count()).group_by(CloudOrder.payment_status)).all())
    delivery_statuses = dict(session.execute(select(CustomerDelivery.status, func.count()).group_by(CustomerDelivery.status)).all())
    transfer_statuses = dict(session.execute(select(TransferJob.status, func.count()).group_by(TransferJob.status)).all())

    live_events = list(
        session.scalars(
            select(Event).where(
                Event.status == "live"
            )
        )
    )

    # Existing Offline/iPad figures.
    offline_browsing = sum(
        int(event.mirrored_customers_browsing or 0)
        for event in live_events
    )

    offline_favourites = sum(
        int(event.mirrored_active_favourites or 0)
        for event in live_events
    )

    offline_baskets = sum(
        int(event.mirrored_active_baskets or 0)
        for event in live_events
    )

    # Online verified customers active in the last 10 minutes.
    online_cutoff = (
        datetime.now(timezone.utc)
        - timedelta(minutes=10)
    )

    online_customers = list(
        session.scalars(
            select(CustomerGalleryActivity)
            .where(
                CustomerGalleryActivity.last_seen_at
                >= online_cutoff
            )
            .order_by(
                CustomerGalleryActivity.last_seen_at.desc()
            )
        )
    )

    online_browsing = len(online_customers)

    online_favourites = sum(
        int(row.favourite_count or 0)
        for row in online_customers
    )

    online_baskets = sum(
        int(row.basket_photo_count or 0)
        for row in online_customers
    )

    # Existing dashboard figures now represent BOTH systems.
    browsing = (
        offline_browsing
        + online_browsing
    )

    favourites = (
        offline_favourites
        + online_favourites
    )

    baskets = (
        offline_baskets
        + online_baskets
    )

    # Use the same device/customer style structure the existing
    # Pirouette dashboard logic already understands.
    online_devices = [
        {
            "device_name": (
                row.customer_name
                or row.customer_email
                or "Online customer"
            ),
            "customer_name": row.customer_name or "",
            "customer_email": row.customer_email or "",
            "page": row.current_area or "gallery",
            "status": "online",
            "basket_count": int(
                row.basket_photo_count or 0
            ),
            "favourites_count": int(
                row.favourite_count or 0
            ),
            "photo_views": int(
                row.photo_views or 0
            ),
            "folder_views": int(
                row.folder_views or 0
            ),
            "last_seen": (
                row.last_seen_at.isoformat()
                if row.last_seen_at
                else ""
            ),
            "source": "online",
        }
        for row in online_customers
    ]

    failed_order_statuses = {"payment_failed", "delivery_failed", "delivery_email_failed"}
    failed_orders = sum(int(order_statuses.get(status, 0)) for status in failed_order_statuses)
    failed_transfers = int(transfer_statuses.get("failed", 0))
    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    stale_live_events = sum(1 for event in live_events if not event.mirrored_last_sync_at or event.mirrored_last_sync_at < stale_cutoff)

    alerts: list[dict[str, str]] = []
    if failed_orders:
        alerts.append({"level": "error", "title": f"{failed_orders} order job(s) need attention", "href": "/staff/orders?queue=failed"})
    if failed_transfers:
        alerts.append({"level": "error", "title": f"{failed_transfers} transfer(s) failed", "href": "/staff/transfers?status_filter=failed"})
    if stale_live_events:
        alerts.append({"level": "warning", "title": f"{stale_live_events} live event mirror(s) are stale", "href": "/staff/events?status_filter=live"})

    today = datetime.now(timezone.utc).date()
    recent_orders = list(session.scalars(select(CloudOrder).order_by(CloudOrder.created_at.desc()).limit(8)))
    paid_today = sum(
        1 for order in recent_orders
        if order.payment_status in {"paid", "verified", "complete"} and order.created_at.date() == today
    )
    completed_today = sum(1 for order in recent_orders if order.completed_at and order.completed_at.date() == today)

    queues = {
        "awaiting_payment": int(payment_statuses.get("awaiting_payment", 0)),
        "print_production": int(order_statuses.get("awaiting_print_production", 0)) + int(order_statuses.get("awaiting_print_production_for_dispatch", 0)),
        "collection": int(order_statuses.get("ready_for_collection", 0)),
        "dispatch": int(order_statuses.get("awaiting_dispatch", 0)),
        "digital_delivery": int(order_statuses.get("waiting_for_download", 0)) + int(order_statuses.get("ready_for_automatic_delivery", 0)),
        "high_res": int(order_statuses.get("awaiting_high_resolution_preparation", 0)),
        "video": int(order_statuses.get("awaiting_video_preparation", 0)),
        "failed": failed_orders + failed_transfers,
        "completed": completed_today,
    }
    delivery_waiting = int(delivery_statuses.get("ready", 0)) + int(delivery_statuses.get("emailed", 0))
    queues["digital_delivery"] += delivery_waiting
    queues["total"] = sum(int(value) for key, value in queues.items() if key not in {"completed"})

    recent_audit = list(session.scalars(select(OrderAudit).order_by(OrderAudit.created_at.desc()).limit(12)))
    return {
        "queues": queues,
        "alerts": alerts,
        "browsing": browsing,
        "favourites": favourites,
        "baskets": baskets,

        # Existing dashboard-compatible activity information.
        "devices": online_devices,
        "online_customers": online_devices,

        # Source breakdown is available to existing dashboard
        # logic without requiring any new visual component.
        "offline_browsing": offline_browsing,
        "online_browsing": online_browsing,
        "offline_favourites": offline_favourites,
        "online_favourites": online_favourites,
        "offline_baskets": offline_baskets,
        "online_baskets": online_baskets,
        "paid_today": paid_today,
        "orders": recent_orders,
        "recent_audit": recent_audit,
        "transfers": {
            "waiting": int(transfer_statuses.get("waiting", 0)),
            "uploading": int(transfer_statuses.get("uploading", 0)),
            "verifying": int(transfer_statuses.get("verifying", 0)),
            "published": int(transfer_statuses.get("complete", 0)),
            "failed": int(transfer_statuses.get("failed", 0)),
        },
    }
