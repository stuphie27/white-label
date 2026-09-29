from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CloudOrder, CustomerDelivery, CustomerFavouriteSession, Event, GalleryAsset
from app.downloads.service import create_delivery
from app.storage import download_to_temp, exists
from app.operations import record_order_audit

TERMINAL_STATUSES = {"completed", "cancelled", "refunded"}


def _items(order: CloudOrder) -> list[dict]:
    try:
        value = json.loads(order.order_items_json or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _asset_paths(session: Session, order: CloudOrder) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    missing: list[str] = []
    for item in _items(order):
        ref = str(item.get("asset_source_ref") or "").strip()
        if not ref:
            continue
        asset = session.scalar(select(GalleryAsset).where(GalleryAsset.source_ref == ref))
        if asset is None:
            missing.append(ref)
            continue
        stored_path = asset.highres_delivery_storage_path if order.product_type == "high_res" else asset.delivery_storage_path
        if not stored_path:
            missing.append(ref)
            continue
        if str(stored_path).startswith("spaces://"):
            try:
                delivery_path = download_to_temp(
                    session.info["pirouette_settings"],
                    str(stored_path),
                    suffix=Path(str(stored_path)).suffix or ".jpg",
                )
            except Exception:
                missing.append(ref)
                continue
        else:
            delivery_path = Path(stored_path)
            if not delivery_path.is_file():
                missing.append(ref)
                continue
        paths.append(delivery_path)
    return paths, missing


def process_paid_order(session: Session, settings, order: CloudOrder) -> CloudOrder:
    """Route a paid order into its correct fulfilment path.

    This function is idempotent: an existing delivery is reused, and local print
    workflow statuses are never overwritten by Cloud delivery processing.
    """
    if order.status in TERMINAL_STATUSES:
        return order
    old_status = order.status
    order.payment_status = "paid"
    order.fulfilment_error = ""

    if (
        order.product_type == "high_res"
        and order.status not in {
            "awaiting_high_resolution_preparation",
            "waiting_for_cloud_assets",
            "ready_for_automatic_delivery",
            "delivery_failed",
            "delivery_email_failed",
        }
    ):
        order.status = "awaiting_high_resolution_preparation"
        record_order_audit(
            session,
            order,
            "fulfilment_routed",
            old_status=old_status,
            new_status=order.status,
            detail="High-resolution order queued for staff preparation",
        )
        session.commit()
        session.refresh(order)
        return order

    if order.product_type in {"low_res", "high_res"}:
        existing = None
        if order.delivery_id:
            existing = session.get(CustomerDelivery, order.delivery_id)
        if existing is None and order.source_ref:
            existing = session.scalar(select(CustomerDelivery).where(CustomerDelivery.source_ref == order.source_ref))
        if existing is not None:
            # A completed/downloaded delivery remains authoritative even after
            # its five-day file-retention period has ended.
            if existing.downloaded_at:
                order.delivery_id = existing.id
                order.status = "completed"
                order.completed_at = existing.downloaded_at
                record_order_audit(
                    session,
                    order,
                    "delivery_reused",
                    old_status=old_status,
                    new_status=order.status,
                    detail="Existing completed secure delivery reused",
                )
                session.commit()
                session.refresh(order)
                return order

            # Waiting deliveries are reusable only while the underlying ZIP
            # still exists. This protects customers from stale /tmp delivery
            # records left behind by an App Platform restart or deployment.
            delivery_available = False
            try:
                delivery_available = bool(
                    existing.zip_path
                    and exists(settings, str(existing.zip_path))
                )
            except Exception as exc:
                order.status = "delivery_storage_error"
                order.fulfilment_error = (
                    "Unable to verify secure delivery storage: "
                    + str(exc)
                )
                record_order_audit(
                    session,
                    order,
                    "delivery_storage_error",
                    old_status=old_status,
                    new_status=order.status,
                    detail=order.fulfilment_error,
                )
                session.commit()
                session.refresh(order)
                return order

            if delivery_available:
                order.delivery_id = existing.id
                order.status = "waiting_for_download"
                record_order_audit(
                    session,
                    order,
                    "delivery_reused",
                    old_status=old_status,
                    new_status=order.status,
                    detail="Existing secure delivery verified and reused",
                )
                session.commit()
                session.refresh(order)
                return order

            # The database record survived but its old temporary ZIP did not.
            # Retire the stale row so its unique source_ref no longer prevents
            # creation of a fresh Spaces-backed delivery.
            stale_source_ref = existing.source_ref

            existing.source_ref = None
            existing.status = "closed"
            existing.deleted_at = datetime.now(timezone.utc)

            order.delivery_id = None

            record_order_audit(
                session,
                order,
                "delivery_missing_rebuild",
                old_status=old_status,
                new_status="rebuilding_delivery",
                detail=(
                    "Secure delivery file was missing; stale delivery retired "
                    "and a new persistent delivery will be created."
                ),
            )

            session.flush()

            # Preserve the order's source reference. Only the stale delivery
            # record relinquishes the unique source_ref.
            if stale_source_ref and not order.source_ref:
                order.source_ref = stale_source_ref

        session.info["pirouette_settings"] = settings
        paths, missing = _asset_paths(session, order)
        if missing or not paths:
            order.status = "waiting_for_cloud_assets"
            label = "high-resolution delivery files" if order.product_type == "high_res" else "low-resolution delivery files"
            order.fulfilment_error = f"Waiting for purchased {label}: " + ", ".join(missing or ["no assets supplied"])
            record_order_audit(session, order, "waiting_for_assets", old_status=old_status, new_status=order.status, detail=order.fulfilment_error)
            session.commit(); session.refresh(order)
            return order

        event = session.get(Event, order.event_id)
        try:
            delivery, _token = create_delivery(
                session,
                settings,
                source_ref=order.source_ref,
                event_name=event.name if event else "",
                order_reference=order.order_reference or order.source_ref or order.id,
                customer_name=order.customer_name,
                customer_email=order.customer_email,
                customer_phone=order.customer_phone,
                delivery_type=order.product_type,
                files=paths,
            )
            order.delivery_id = delivery.id
            order.status = "waiting_for_download" if delivery.emailed_at else "delivery_email_failed"
            if not delivery.emailed_at:
                order.fulfilment_error = "ZIP created, but the delivery email could not be sent."
        except Exception as exc:
            order.retry_count += 1
            order.status = "delivery_failed"
            order.fulfilment_error = str(exc)[:1000]
        record_order_audit(session, order, "fulfilment_routed", old_status=old_status, new_status=order.status, detail=order.fulfilment_error)
        session.commit(); session.refresh(order)
        return order

    if order.product_type == "favourites_extension":
        now = datetime.now(timezone.utc)
        for item in _items(order):
            token = str(item.get("favourite_token") or "").strip()
            if not token:
                continue
            favourite_session = session.scalar(select(CustomerFavouriteSession).where(CustomerFavouriteSession.token == token))
            if favourite_session:
                favourite_session.extension_requested = True
                favourite_session.extension_paid = True
                favourite_session.retention_choice = "vault_year"
                favourite_session.expires_at = now + timedelta(days=35)
        order.status = "completed"
        order.completed_at = now
    elif order.product_type == "high_res":
        order.status = "waiting_for_cloud_assets"
    elif order.product_type == "print" and order.fulfilment_method == "event_collection":
        order.status = "awaiting_print_production"
    elif order.product_type == "print":
        order.status = "awaiting_print_production_for_dispatch"
    elif order.product_type == "video":
        order.status = "awaiting_video_preparation"
    else:
        order.status = "awaiting_staff_fulfilment"
    record_order_audit(session, order, "fulfilment_routed", old_status=old_status, new_status=order.status)
    session.commit(); session.refresh(order)
    return order


def status_payload(order: CloudOrder) -> dict[str, object]:
    return {
        "source_ref": order.source_ref,
        "order_reference": order.order_reference,
        "payment_status": order.payment_status,
        "payment_amount_pence": order.payment_amount_pence,
        "sumup_status": order.sumup_status,
        "sumup_transaction_code": order.sumup_transaction_code,
        "payment_verified_at": order.payment_verified_at.isoformat() if order.payment_verified_at else None,
        "status": order.status,
        "fulfilment_method": order.fulfilment_method,
        "delivery_id": order.delivery_id,
        "fulfilment_error": order.fulfilment_error,
        "retry_count": order.retry_count,
        "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }
