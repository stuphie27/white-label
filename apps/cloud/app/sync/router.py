from __future__ import annotations

import hmac
import logging
import os
import re
from pathlib import Path
from datetime import date, time, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import unquote

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.db.models import CustomerFavourite, CustomerFavouriteSession, Event, EventAvailabilityRequest, Gallery, GalleryAsset, GalleryFolder, StaffPayrollSubmission, SyncAsset, TransferJob
from app.db.models import CustomerRewardSnapshot
from app.db.session import database_schema_is_ready
from app.sync.service import (
    create_or_get_transfer,
    register_manifest,
    transfer_resume_state,
    update_asset_progress,
    upsert_event,
    upsert_gallery,
)
from app.storage import delete, exists, upload_bytes, spaces_enabled, presigned_put_url
from app.db.models import CustomerGalleryActivity, Gallery


logger = logging.getLogger(__name__)


class ProductOfferSync(BaseModel):
    quantity: int = Field(ge=2, le=100)
    price_pence: int = Field(ge=0, le=10000000)
    active: bool = True


class ProductSync(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=120)
    name: str = Field(min_length=1, max_length=220)
    price_pence: int = Field(ge=0, le=10000000)
    fulfilment_type: str = Field(default="print", max_length=50)
    quick_checkout: bool = False
    sold_out: bool = False
    sort_order: int = 0
    offers: list[ProductOfferSync] = Field(default_factory=list, max_length=30)


class EventSync(BaseModel):
    master_source_ref: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    client_name: str = Field(default="", max_length=200)
    principal_name: str = Field(default="", max_length=200)
    photographer_name: str = Field(default="", max_length=200)
    dance_style: str = Field(default="", max_length=120)
    brand_id: str = Field(default="sophies", max_length=80)
    start_date: date
    end_date: date
    arrival_time: time | None = None
    photography_start: time | None = None
    photography_finish: time | None = None
    venue: str = Field(default="", max_length=250)
    description: str = ""
    internal_notes: str = ""
    event_logo_storage_path: str = ""
    status: Literal["draft", "live", "processing", "complete", "archived"] = "draft"
    service_desk_payments_enabled: bool = False
    event_collection_available: bool = False
    personal_video_enabled: bool = False
    personal_video_type: Literal[
        "",
        "ballroom",
        "freestyle",
        "theatre",
    ] = ""
    customers_browsing: int = Field(default=0, ge=0)
    active_favourites: int = Field(default=0, ge=0)
    active_baskets: int = Field(default=0, ge=0)
    orders_today: int = Field(default=0, ge=0)
    pricing_catalogue: list[ProductSync] = Field(default_factory=list, max_length=200)

    @field_validator("end_date")
    @classmethod
    def valid_dates(cls, value: date, info):
        start = info.data.get("start_date")
        if start and value < start:
            raise ValueError("end_date cannot be before start_date")
        return value


class GallerySync(BaseModel):
    event_source_ref: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(default="", max_length=180)
    status: Literal["draft", "uploading", "ready", "published", "archived"] = "draft"
    visibility: Literal["private", "public"] = "private"
    expires_at: date | None = None
    description: str = ""


class CustomerRewardSnapshotSync(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    points: int = Field(default=0, ge=0, le=100000000)


class CustomerRewardSnapshotBatch(BaseModel):
    snapshots: list[CustomerRewardSnapshotSync] = Field(
        default_factory=list,
        max_length=5000,
    )


class TransferSync(BaseModel):
    source_ref: str = Field(min_length=1, max_length=200)
    gallery_source_ref: str = Field(min_length=1, max_length=200)
    destination: Literal["customer", "zenfolio"] = "customer"


class AssetManifest(BaseModel):
    source_ref: str = Field(min_length=1, max_length=240)
    filename: str = Field(min_length=1, max_length=500)
    size_bytes: int = Field(default=0, ge=0)
    sha256: str = Field(default="", pattern=r"^$|^[a-fA-F0-9]{64}$")


class ManifestSync(BaseModel):
    assets: list[AssetManifest] = Field(default_factory=list, max_length=10000)


class FolderManifestItem(BaseModel):
    media_kind: Literal["photos", "videos"] = "photos"
    path: str = Field(default="", max_length=800)


class FolderManifestSync(BaseModel):
    folders: list[FolderManifestItem] = Field(default_factory=list, max_length=20000)


class FavouriteSessionSync(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    customer_name: str = Field(default="", max_length=200)
    retention_choice: str = Field(default="temporary_link", max_length=30)
    expires_at: datetime | None = None
    extension_paid: bool = False
    asset_source_refs: list[str] = Field(default_factory=list, max_length=10000)


class FavouritePreviewPurgeSync(BaseModel):
    keep_source_refs: list[str] = Field(default_factory=list, max_length=100000)


class ProgressSync(BaseModel):
    bytes_received: int = Field(ge=0)
    status: Literal["waiting", "uploading", "verified", "complete", "failed", "expired"]


def _authorise(request: Request, supplied: str | None) -> None:
    configured = request.app.state.settings.sync_api_key
    if not configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sync API is not configured")
    if not supplied or not hmac.compare_digest(configured, supplied):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid sync API key")
    engine = getattr(request.app.state, "engine", None)
    if engine is None or not database_schema_is_ready(engine):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database schema is not ready")


def build_sync_router() -> APIRouter:
    router = APIRouter(prefix="/api/sync/v1", tags=["sync"])

    @router.get("/status")
    async def sync_status(request: Request, x_pirouette_sync_key: str | None = Header(default=None)):
        _authorise(request, x_pirouette_sync_key)
        return {"ok": True, "version": request.app.state.settings.version, "retention_days": 5, "stores_files": False}

    @router.put("/customer-rewards/snapshots")
    async def sync_customer_reward_snapshots(
        payload: CustomerRewardSnapshotBatch,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Receive authoritative Sophie’s Rewards balances from Offline."""

        _authorise(
            request,
            x_pirouette_sync_key,
        )

        updated = 0

        with request.app.state.session_factory() as session:

            for item in payload.snapshots:

                email = str(
                    item.email or ""
                ).strip().lower()

                if not email or "@" not in email:
                    continue

                snapshot = session.get(
                    CustomerRewardSnapshot,
                    email,
                )

                if snapshot is None:

                    snapshot = CustomerRewardSnapshot(
                        email=email,
                        points=max(
                            0,
                            int(item.points or 0),
                        ),
                    )

                    session.add(snapshot)

                else:

                    snapshot.points = max(
                        0,
                        int(item.points or 0),
                    )

                    snapshot.updated_at = datetime.now(
                        timezone.utc
                    )

                updated += 1

            session.commit()

        return {
            "ok": True,
            "updated": updated,
        }


    @router.get("/customer-activity/live")
    async def live_customer_activity(
        request: Request,
        x_pirouette_sync_key: str | None = Header(
            default=None
        ),
    ):
        _authorise(
            request,
            x_pirouette_sync_key,
        )

        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(minutes=10)
        )

        with request.app.state.session_factory() as session:
            rows = session.execute(
                select(
                    CustomerGalleryActivity,
                    Gallery,
                )
                .join(
                    Gallery,
                    Gallery.id
                    == CustomerGalleryActivity.gallery_id,
                )
                .where(
                    CustomerGalleryActivity.last_seen_at
                    >= cutoff
                )
                .order_by(
                    CustomerGalleryActivity.last_seen_at.desc()
                )
            ).all()

            customers = []
            now = datetime.now(timezone.utc)

            for activity, gallery in rows:
                seen = activity.last_seen_at

                if seen is not None and seen.tzinfo is None:
                    seen = seen.replace(
                        tzinfo=timezone.utc
                    )

                idle = (
                    max(
                        0,
                        int(
                            (now - seen).total_seconds()
                        ),
                    )
                    if seen is not None
                    else 0
                )

                customers.append({
                    "session_token":
                        "online:" + str(activity.id),

                    "device_id":
                        "online:"
                        + str(
                            activity.customer_identity_id
                        ),

                    "device_name":
                        str(
                            activity.customer_name
                            or "Online customer"
                        ),

                    "event_slug":
                        str(gallery.slug or ""),

                    "page":
                        str(
                            activity.current_area
                            or "gallery"
                        ),

                    "status": "online",

                    "basket_count":
                        int(
                            activity.basket_photo_count
                            or 0
                        ),

                    "favourites_count":
                        int(
                            activity.favourite_count
                            or 0
                        ),

                    "photo_views":
                        int(
                            activity.photo_views
                            or 0
                        ),

                    "folder_views":
                        int(
                            activity.folder_views
                            or 0
                        ),

                    "idle_seconds": idle,

                    "first_seen":
                        activity.first_seen_at.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        if activity.first_seen_at
                        else "",

                    "last_seen":
                        seen.astimezone(
                            timezone.utc
                        ).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        if seen
                        else "",

                    "locked": False,
                    "last_message": "",
                    "battery_level": None,
                    "is_charging": False,
                    "connection_type": "Cloud",
                    "network_quality": "Online",
                    "operator_name": "Online customer",
                    "ballroom": "",
                    "upload_current": 0,
                    "upload_total": 0,

                    "current_gallery":
                        str(
                            activity.current_folder
                            or gallery.name
                            or ""
                        ),

                    "source": "online",
                })

            return {
                "customers": customers,
                "count": len(customers),
                "active_minutes": 10,
            }


    @router.get("/handoff/events/next")
    async def next_online_event(
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """
        Read-only STUPHIE Online -> Offline event handoff.

        Online remains the authoritative event master.

        Only an event explicitly confirmed as Event Ready can be handed
        to Offline. Completed and archived events are never selected.
        The earliest ready event is returned first.
        """
        _authorise(request, x_pirouette_sync_key)

        today = date.today()
        exclude_source_ref = str(
            request.query_params.get("exclude_source_ref") or ""
        ).strip()

        with request.app.state.session_factory() as session:
            statement = (
                select(Event)
                .where(
                    Event.status.notin_(("complete", "archived", "cancelled")),
                    Event.end_date >= today,
                )
                .order_by(
                    Event.start_date.asc(),
                    Event.created_at.asc(),
                )
                .limit(1)
            )

            if exclude_source_ref:
                statement = statement.where(
                    Event.source_ref != exclude_source_ref
                )

            event = session.scalar(statement)

            if event is None:
                return {
                    "event": None,
                    "status": "no_ready_event",
                }

            return {
                "event": {
                    "id": event.id,
                    "source_ref": event.source_ref,
                    "name": event.name,
                    "client_name": event.client_name,
                    "principal_name": event.principal_name,
                    "photographer_name": event.photographer_name,
                    "dance_style": event.dance_style,
                    "start_date": event.start_date,
                    "end_date": event.end_date,
                    "arrival_time": event.arrival_time,
                    "photography_start": event.photography_start,
                    "photography_finish": event.photography_finish,
                    "venue": event.venue,
                    "description": event.description,
                    "internal_notes": event.internal_notes,
                    "status": event.status,
                    "promoter_name": event.promoter_name,
                    "promoter_email": event.promoter_email,
                    "promoter_phone": event.promoter_phone,
                    "staff_required": event.staff_required,
                    "event_start_time": event.event_start_time,
                    "event_end_time": event.event_end_time,
                    "travel_time_minutes": event.travel_time_minutes,
                    "hotel_required": event.hotel_required,
                    "hotel_info": event.hotel_info,
                    "planning_notes": event.planning_notes,
                    "photography_enabled": event.photography_enabled,
                    "video_enabled": event.video_enabled,
                    "printing_enabled": event.printing_enabled,
                    "customer_kiosk_enabled": event.customer_kiosk_enabled,
                    "event_ready": event.event_ready,
                    "event_ready_at": event.event_ready_at,
                },
                "status": "ready",
            }

    @router.put("/events/{source_ref}")
    async def sync_event(source_ref: str, payload: EventSync, request: Request, x_pirouette_sync_key: str | None = Header(default=None)):
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            values = payload.model_dump()
            values.pop("event_logo_storage_path", None)
            values["mirrored_customers_browsing"] = values.pop("customers_browsing")
            values["mirrored_active_favourites"] = values.pop("active_favourites")
            values["mirrored_active_baskets"] = values.pop("active_baskets")
            values["mirrored_orders_today"] = values.pop("orders_today")
            import json
            values["pricing_json"] = json.dumps(values.pop("pricing_catalogue", []), separators=(",", ":"))
            values["mirrored_last_sync_at"] = datetime.now(timezone.utc)
            event = upsert_event(session, source_ref=source_ref, values=values)
        return {"id": event.id, "source_ref": event.source_ref, "status": event.status, "mirror": "accepted"}

    @router.put("/galleries/{source_ref}")
    async def sync_gallery(source_ref: str, payload: GallerySync, request: Request, x_pirouette_sync_key: str | None = Header(default=None)):
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            event = session.scalar(select(Event).where(Event.source_ref == payload.event_source_ref))
            if event is None:
                raise HTTPException(status_code=404, detail="Event source reference not found")
            values = payload.model_dump(exclude={"event_source_ref"})
            gallery = upsert_gallery(session, source_ref=source_ref, event=event, values=values)
        return {"id": gallery.id, "source_ref": gallery.source_ref, "slug": gallery.slug, "status": gallery.status}

    @router.put("/galleries/{gallery_ref}/folders")
    async def sync_gallery_folders(
        gallery_ref: str, payload: FolderManifestSync, request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == gallery_ref))
            if gallery is None:
                raise HTTPException(status_code=404, detail="Gallery source reference not found")
            existing = list(session.scalars(select(GalleryFolder).where(GalleryFolder.gallery_id == gallery.id)))
            by_key = {(row.media_kind, row.path): row for row in existing}
            for row in existing:
                row.active = False
            accepted = 0
            for index, item in enumerate(payload.folders):
                path = "/".join(part for part in item.path.replace("\\", "/").strip("/").split("/") if part not in ("", ".", ".."))
                if not path:
                    continue
                parent = path.rsplit("/", 1)[0] if "/" in path else ""
                name = path.rsplit("/", 1)[-1]
                folder = by_key.get((item.media_kind, path))
                if folder is None:
                    folder = GalleryFolder(gallery_id=gallery.id, media_kind=item.media_kind, path=path, parent_path=parent, name=name)
                    session.add(folder)
                folder.parent_path = parent
                folder.name = name
                folder.active = True
                folder.sort_order = index
                accepted += 1
            session.commit()
        return {"gallery_source_ref": gallery_ref, "folders": accepted, "status": "accepted"}

    @router.get("/galleries/{gallery_ref}/favourite-session-by-email")
    async def read_favourite_session_by_email(
        gallery_ref: str,
        email: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Read one Cloud favourites session by customer email.

        Read-only recovery endpoint for Cloud-created favourite sessions whose
        token differs from the corresponding Offline session token.
        """
        _authorise(request, x_pirouette_sync_key)

        clean_email = str(email or "").strip().lower()
        if not clean_email or "@" not in clean_email:
            raise HTTPException(
                status_code=400,
                detail="Invalid customer email",
            )

        with request.app.state.session_factory() as session:
            gallery = session.scalar(
                select(Gallery).where(
                    Gallery.source_ref == gallery_ref
                )
            )

            if gallery is None:
                raise HTTPException(
                    status_code=404,
                    detail="Gallery source reference not found",
                )

            favourite = session.scalar(
                select(CustomerFavouriteSession).where(
                    CustomerFavouriteSession.gallery_id == gallery.id,
                    CustomerFavouriteSession.email == clean_email,
                )
            )

            if favourite is None:
                raise HTTPException(
                    status_code=404,
                    detail="Favourite session not found",
                )

            rows = list(
                session.execute(
                    select(CustomerFavourite, GalleryAsset)
                    .join(
                        GalleryAsset,
                        GalleryAsset.id == CustomerFavourite.asset_id,
                    )
                    .where(
                        CustomerFavourite.session_id == favourite.id,
                        GalleryAsset.gallery_id == gallery.id,
                    )
                    .order_by(
                        CustomerFavourite.created_at,
                        CustomerFavourite.id,
                    )
                ).all()
            )

            assets = [
                {
                    "asset_id": str(asset.id),
                    "source_ref": str(asset.source_ref or ""),
                    "filename": str(asset.filename or ""),
                    "folder_path": str(asset.folder_path or ""),
                    "status": str(asset.status or ""),
                    "created_at": (
                        saved.created_at.isoformat()
                        if saved.created_at
                        else None
                    ),
                }
                for saved, asset in rows
            ]

            return {
                "gallery_source_ref": gallery_ref,
                "token": favourite.token,
                "email": favourite.email,
                "customer_name": favourite.customer_name,
                "retention_choice": favourite.retention_choice,
                "expires_at": (
                    favourite.expires_at.isoformat()
                    if favourite.expires_at
                    else None
                ),
                "favourite_count": len(assets),
                "assets": assets,
            }


    @router.get("/galleries/{gallery_ref}/favourite-sessions/{token}")
    async def read_favourite_session(
        gallery_ref: str,
        token: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Read one Cloud favourites session for authenticated Offline sync.

        This endpoint is deliberately read-only. It never creates, updates or
        removes a favourite session or any favourite asset.
        """
        _authorise(request, x_pirouette_sync_key)

        clean_token = str(token or "").strip()
        if not clean_token or len(clean_token) > 80:
            raise HTTPException(status_code=400, detail="Invalid favourites token")

        with request.app.state.session_factory() as session:
            gallery = session.scalar(
                select(Gallery).where(Gallery.source_ref == gallery_ref)
            )
            if gallery is None:
                raise HTTPException(
                    status_code=404,
                    detail="Gallery source reference not found",
                )

            favourite = session.scalar(
                select(CustomerFavouriteSession).where(
                    CustomerFavouriteSession.gallery_id == gallery.id,
                    CustomerFavouriteSession.token == clean_token,
                )
            )
            if favourite is None:
                raise HTTPException(
                    status_code=404,
                    detail="Favourite session not found",
                )

            rows = list(
                session.execute(
                    select(CustomerFavourite, GalleryAsset)
                    .join(
                        GalleryAsset,
                        GalleryAsset.id == CustomerFavourite.asset_id,
                    )
                    .where(
                        CustomerFavourite.session_id == favourite.id,
                        GalleryAsset.gallery_id == gallery.id,
                    )
                    .order_by(
                        CustomerFavourite.created_at,
                        CustomerFavourite.id,
                    )
                ).all()
            )

            assets = [
                {
                    "asset_id": str(asset.id),
                    "source_ref": str(asset.source_ref or ""),
                    "filename": str(asset.filename or ""),
                    "folder_path": str(asset.folder_path or ""),
                    "status": str(asset.status or ""),
                    "created_at": (
                        saved.created_at.isoformat()
                        if saved.created_at
                        else None
                    ),
                }
                for saved, asset in rows
            ]

            return {
                "gallery_source_ref": gallery_ref,
                "token": favourite.token,
                "email": favourite.email,
                "customer_name": favourite.customer_name,
                "retention_choice": favourite.retention_choice,
                "expires_at": (
                    favourite.expires_at.isoformat()
                    if favourite.expires_at
                    else None
                ),
                "favourite_count": len(assets),
                "assets": assets,
            }


    @router.put("/galleries/{gallery_ref}/favourite-sessions/{token}")
    async def sync_favourite_session(
        gallery_ref: str,
        token: str,
        payload: FavouriteSessionSync,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Idempotently mirror one Offline favourites entitlement into Cloud.

        Source refs are resolved only inside the requested gallery. Unknown or
        cross-gallery refs reject the whole update so the Offline Mac can retry
        after its normal preview upload has caught up.
        """
        _authorise(request, x_pirouette_sync_key)
        clean_token = str(token or "").strip()
        if not clean_token or len(clean_token) > 80:
            raise HTTPException(status_code=400, detail="Invalid favourites token")
        clean_refs = list(dict.fromkeys(str(ref or "").strip() for ref in payload.asset_source_refs if str(ref or "").strip()))

        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == gallery_ref))
            if gallery is None:
                raise HTTPException(status_code=404, detail="Gallery source reference not found")

            # Favourites are customer data, not an asset-readiness state.
            #
            # Resolve every referenced GalleryAsset that still exists in this
            # gallery regardless of its temporary ready/missing/recovery
            # status. A recovering preview must never prevent the customer's
            # favourites session itself from being reconciled.
            assets = list(session.scalars(select(GalleryAsset).where(
                GalleryAsset.gallery_id == gallery.id,
                GalleryAsset.source_ref.in_(clean_refs),
            ))) if clean_refs else []
            by_ref = {asset.source_ref: asset for asset in assets}
            unresolved_refs = [
                ref for ref in clean_refs
                if ref not in by_ref
            ]

            favourite = session.scalar(select(CustomerFavouriteSession).where(
                CustomerFavouriteSession.gallery_id == gallery.id,
                CustomerFavouriteSession.token == clean_token,
            ))
            if favourite is None:
                favourite = session.scalar(select(CustomerFavouriteSession).where(
                    CustomerFavouriteSession.gallery_id == gallery.id,
                    CustomerFavouriteSession.email == payload.email.strip().lower(),
                ))
            if favourite is None:
                favourite = CustomerFavouriteSession(
                    gallery_id=gallery.id,
                    token=clean_token,
                    email=payload.email.strip().lower(),
                )
                session.add(favourite)
                session.flush()
            elif favourite.token != clean_token:
                favourite.token = clean_token

            favourite.email = payload.email.strip().lower()
            favourite.customer_name = payload.customer_name.strip()
            favourite.retention_choice = payload.retention_choice or "temporary_link"
            favourite.extension_paid = bool(payload.extension_paid)
            favourite.extension_requested = favourite.retention_choice in {"vault_year", "extension_35", "extension_70"}
            favourite.expires_at = payload.expires_at

            existing = list(session.scalars(select(CustomerFavourite).where(
                CustomerFavourite.session_id == favourite.id
            )))
            # Offline -> Cloud reconciliation is deliberately additive.
            #
            # Never delete an existing Cloud favourite merely because it is
            # absent from one Offline payload. Cloud may contain favourites
            # created online that have not yet reached the event Mac.
            existing_ids = {row.asset_id for row in existing}
            for asset in assets:
                if asset.id not in existing_ids:
                    session.add(
                        CustomerFavourite(
                            session_id=favourite.id,
                            asset_id=asset.id,
                        )
                    )
            session.commit()
            session.refresh(favourite)

            authoritative_rows = list(
                session.execute(
                    select(CustomerFavourite, GalleryAsset)
                    .join(
                        GalleryAsset,
                        GalleryAsset.id == CustomerFavourite.asset_id,
                    )
                    .where(
                        CustomerFavourite.session_id == favourite.id,
                        GalleryAsset.gallery_id == gallery.id,
                    )
                    .order_by(
                        CustomerFavourite.created_at,
                        CustomerFavourite.id,
                    )
                ).all()
            )

            authoritative_refs = [
                str(asset.source_ref or "")
                for saved, asset in authoritative_rows
                if str(asset.source_ref or "").strip()
            ]

        return {
            "gallery_source_ref": gallery_ref,
            "token": favourite.token,
            "favourite_count": len(authoritative_refs),
            "status": "accepted",
            "unresolved_source_refs": unresolved_refs,
            "authoritative_asset_source_refs": authoritative_refs,
        }

    @router.get("/galleries/{gallery_ref}/health")
    async def gallery_health(
        gallery_ref: str, request: Request, x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Report whether mirrored gallery records still have real files behind them.

        DigitalOcean App Platform local storage is ephemeral. A restart may leave
        PostgreSQL rows intact while removing files from the container filesystem.
        Mark those rows missing so public pages never advertise broken assets and
        let the offline Mac reconcile/re-upload them automatically.
        """
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == gallery_ref))
            if gallery is None:
                raise HTTPException(status_code=404, detail="Gallery source reference not found")
            assets = list(session.scalars(select(GalleryAsset).where(GalleryAsset.gallery_id == gallery.id)))
            available_source_refs: list[str] = []
            missing_source_refs: list[str] = []
            for asset in assets:
                asset_available = exists(
                    request.app.state.settings,
                    str(asset.storage_path or ""),
                )

                if asset_available:
                    available_source_refs.append(asset.source_ref)
                    if asset.status == "missing":
                        asset.status = "ready"
                else:
                    missing_source_refs.append(asset.source_ref)
                    if asset.status == "ready":
                        asset.status = "missing"
            folder_count = len(list(session.scalars(select(GalleryFolder).where(
                GalleryFolder.gallery_id == gallery.id, GalleryFolder.active.is_(True)
            ))))
            session.commit()
        return {
            "gallery_source_ref": gallery_ref,
            "gallery_status": gallery.status,
            "visibility": gallery.visibility,
            "slug": gallery.slug,
            "asset_record_count": len(assets),
            "available_file_count": len(available_source_refs),
            "missing_file_count": len(missing_source_refs),
            "available_source_refs": available_source_refs,
            "missing_source_refs": missing_source_refs,
            "folder_count": folder_count,
        }

    @router.post("/galleries/{gallery_ref}/purge-non-favourites")
    async def purge_non_favourite_previews(
        gallery_ref: str, payload: FavouritePreviewPurgeSync, request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Remove public preview files that are not used by any customer favourite.

        Only the public watermarked preview is removed. Private paid-delivery
        copies are deliberately retained, and the GalleryAsset row remains so a
        photograph can be re-uploaded safely if a customer favourites it later.
        """
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == gallery_ref))
            if gallery is None:
                raise HTTPException(status_code=404, detail="Gallery source reference not found")

            favourite_asset_ids = set(session.scalars(
                select(CustomerFavourite.asset_id)
                .join(CustomerFavouriteSession, CustomerFavourite.session_id == CustomerFavouriteSession.id)
                .where(CustomerFavouriteSession.gallery_id == gallery.id)
            ))
            keep_refs = {str(ref or "").strip() for ref in payload.keep_source_refs if str(ref or "").strip()}
            if keep_refs:
                favourite_asset_ids.update(session.scalars(select(GalleryAsset.id).where(
                    GalleryAsset.gallery_id == gallery.id,
                    GalleryAsset.source_ref.in_(keep_refs),
                )))
            assets = list(session.scalars(select(GalleryAsset).where(
                GalleryAsset.gallery_id == gallery.id,
                GalleryAsset.media_kind == "photos",
            )))
            removed = 0
            already_removed = 0
            kept = 0
            for asset in assets:
                if asset.id in favourite_asset_ids:
                    kept += 1
                    continue
                if str(asset.storage_path or ""):
                    delete(request.app.state.settings, str(asset.storage_path))
                    removed += 1
                else:
                    already_removed += 1
                asset.storage_path = ""
                asset.size_bytes = 0
                asset.status = "removed"
            session.commit()

        return {
            "gallery_source_ref": gallery_ref,
            "removed_preview_count": removed,
            "already_removed_count": already_removed,
            "favourite_preview_count": kept,
            "status": "complete",
        }

    @router.post("/galleries/{gallery_ref}/online")
    async def put_gallery_online(
        gallery_ref: str, request: Request, x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Publish an already-mirrored event without requiring another upload cycle."""
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == gallery_ref))
            if gallery is None:
                raise HTTPException(status_code=404, detail="Gallery source reference not found")
            gallery.status = "published"
            gallery.visibility = "public"
            session.commit()
        return {"gallery_source_ref": gallery_ref, "status": "published"}

    @router.post("/galleries/{gallery_ref}/offline")
    async def take_gallery_offline(
        gallery_ref: str, request: Request, x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Unpublish a public event without deleting its mirrored files or folders."""
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == gallery_ref))
            if gallery is None:
                raise HTTPException(status_code=404, detail="Gallery source reference not found")
            gallery.status = "ready"
            session.commit()
        return {"gallery_source_ref": gallery_ref, "status": "offline"}

    @router.post("/transfers")
    async def sync_transfer(payload: TransferSync, request: Request, x_pirouette_sync_key: str | None = Header(default=None)):
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == payload.gallery_source_ref))
            if gallery is None:
                raise HTTPException(status_code=404, detail="Gallery source reference not found")
            job = create_or_get_transfer(session, source_ref=payload.source_ref, gallery=gallery, destination=payload.destination)
        return {"id": job.id, "source_ref": job.source_ref, "status": job.status, "expires_at": job.expires_at}

    @router.put("/transfers/{source_ref}/manifest")
    async def sync_manifest(source_ref: str, payload: ManifestSync, request: Request, x_pirouette_sync_key: str | None = Header(default=None)):
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            job = session.scalar(select(TransferJob).where(TransferJob.source_ref == source_ref))
            if job is None:
                raise HTTPException(status_code=404, detail="Transfer source reference not found")
            assets = register_manifest(session, job=job, assets=[asset.model_dump() for asset in payload.assets])
        return {"transfer_id": job.id, "item_count": len(assets), "bytes_total": job.bytes_total}

    @router.get("/transfers/{source_ref}/resume")
    async def sync_resume(source_ref: str, request: Request, x_pirouette_sync_key: str | None = Header(default=None)):
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            job = session.scalar(select(TransferJob).where(TransferJob.source_ref == source_ref))
            if job is None:
                raise HTTPException(status_code=404, detail="Transfer source reference not found")
            assets = transfer_resume_state(session, job)
            return {
                "transfer": {"id": job.id, "status": job.status, "bytes_total": job.bytes_total, "bytes_transferred": job.bytes_transferred, "expires_at": job.expires_at},
                "assets": [{"source_ref": a.source_ref, "filename": a.filename, "size_bytes": a.size_bytes, "bytes_received": a.bytes_received, "status": a.status, "sha256": a.sha256} for a in assets],
            }

    @router.patch("/transfers/{source_ref}/assets/{asset_ref}")
    async def sync_progress(source_ref: str, asset_ref: str, payload: ProgressSync, request: Request, x_pirouette_sync_key: str | None = Header(default=None)):
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            job = session.scalar(select(TransferJob).where(TransferJob.source_ref == source_ref))
            if job is None:
                raise HTTPException(status_code=404, detail="Transfer source reference not found")
            asset = session.scalar(select(SyncAsset).where(SyncAsset.transfer_job_id == job.id, SyncAsset.source_ref == asset_ref))
            if asset is None:
                raise HTTPException(status_code=404, detail="Asset source reference not found")
            asset = update_asset_progress(session, asset=asset, bytes_received=payload.bytes_received, status=payload.status)
        return {"source_ref": asset.source_ref, "bytes_received": asset.bytes_received, "status": asset.status}


    @router.get("/galleries/{gallery_ref}/assets/{asset_ref}/metadata")
    async def gallery_asset_metadata(
        gallery_ref: str,
        asset_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Return private asset metadata to the authenticated Event Mac."""
        _authorise(request, x_pirouette_sync_key)

        with request.app.state.session_factory() as session:
            gallery = session.scalar(
                select(Gallery).where(
                    Gallery.source_ref == gallery_ref
                )
            )

            if gallery is None:
                raise HTTPException(
                    status_code=404,
                    detail="Gallery source reference not found",
                )

            asset = session.scalar(
                select(GalleryAsset).where(
                    GalleryAsset.gallery_id == gallery.id,
                    GalleryAsset.source_ref == asset_ref,
                )
            )

            if asset is None:
                raise HTTPException(
                    status_code=404,
                    detail="Gallery asset source reference not found",
                )

            return {
                "id": asset.id,
                "source_ref": asset.source_ref,
                "filename": asset.filename,
                "folder_path": asset.folder_path,
                "media_kind": asset.media_kind,
                "size_bytes": int(asset.size_bytes or 0),
                "status": asset.status,
                "public_preview_available": bool(asset.storage_path),
                "lowres_delivery_available": bool(
                    asset.delivery_storage_path
                ),
                "highres_delivery_available": bool(
                    asset.highres_delivery_storage_path
                ),
            }


    @router.post("/galleries/{gallery_ref}/assets/{asset_ref}/direct-upload")
    async def prepare_gallery_asset_direct_upload(
        gallery_ref: str,
        asset_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
        x_pirouette_filename: str | None = Header(default="image.webp"),
        x_pirouette_content_type: str | None = Header(default="image/webp"),
    ):
        """Issue a short-lived direct Spaces upload URL for one public preview."""
        _authorise(request, x_pirouette_sync_key)

        settings = request.app.state.settings

        if not spaces_enabled(settings):
            raise HTTPException(
                status_code=503,
                detail="Direct public preview storage is unavailable",
            )

        safe_gallery = (
            re.sub(
                r"[^A-Za-z0-9._-]+",
                "-",
                gallery_ref,
            ).strip("-")
            or "gallery"
        )

        safe_ref = (
            re.sub(
                r"[^A-Za-z0-9._-]+",
                "-",
                asset_ref,
            ).strip("-")
            or "asset"
        )

        suffix = (
            Path(
                x_pirouette_filename
                or "image.webp"
            ).suffix.lower()
            or ".webp"
        )

        content_type = (
            x_pirouette_content_type
            or "image/webp"
        ).strip() or "image/webp"

        object_key = (
            f"gallery-previews/"
            f"{safe_gallery}/"
            f"{safe_ref}{suffix}"
        )

        with request.app.state.session_factory() as session:
            gallery = session.scalar(
                select(Gallery).where(
                    Gallery.source_ref == gallery_ref
                )
            )

            if gallery is None:
                raise HTTPException(
                    status_code=404,
                    detail="Gallery source reference not found",
                )

        upload_url = presigned_put_url(
            settings,
            object_key,
            content_type=content_type,
            expires_seconds=900,
        )

        return {
            "source_ref": asset_ref,
            "object_key": object_key,
            "upload_url": upload_url,
            "content_type": content_type,
            "expires_seconds": 900,
        }


    @router.post("/galleries/{gallery_ref}/assets/{asset_ref}/direct-upload/complete")
    async def complete_gallery_asset_direct_upload(
        gallery_ref: str,
        asset_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
        x_pirouette_filename: str | None = Header(default="image.webp"),
        x_pirouette_content_type: str | None = Header(default="image/webp"),
        x_pirouette_folder_path: str | None = Header(default=""),
        x_pirouette_media_kind: str | None = Header(default="photos"),
        x_pirouette_size_bytes: str | None = Header(default="0"),
    ):
        """Verify one direct Spaces upload and attach it to the gallery record."""
        _authorise(request, x_pirouette_sync_key)

        settings = request.app.state.settings

        if not spaces_enabled(settings):
            raise HTTPException(
                status_code=503,
                detail="Direct public preview storage is unavailable",
            )

        folder_path = "/".join(
            part
            for part in unquote(
                x_pirouette_folder_path or ""
            )
            .replace("\\", "/")
            .strip("/")
            .split("/")
            if part not in ("", ".", "..")
        )

        media_kind = (
            "videos"
            if (
                x_pirouette_media_kind
                or ""
            ).lower() == "videos"
            else "photos"
        )

        safe_gallery = (
            re.sub(
                r"[^A-Za-z0-9._-]+",
                "-",
                gallery_ref,
            ).strip("-")
            or "gallery"
        )

        safe_ref = (
            re.sub(
                r"[^A-Za-z0-9._-]+",
                "-",
                asset_ref,
            ).strip("-")
            or "asset"
        )

        suffix = (
            Path(
                x_pirouette_filename
                or "image.webp"
            ).suffix.lower()
            or ".webp"
        )

        content_type = (
            x_pirouette_content_type
            or "image/webp"
        ).strip() or "image/webp"

        try:
            size_bytes = max(
                0,
                int(
                    x_pirouette_size_bytes
                    or "0"
                ),
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid preview size",
            )

        if size_bytes > settings.max_live_asset_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Asset exceeds the configured "
                    "live-gallery limit"
                ),
            )

        object_key = (
            f"gallery-previews/"
            f"{safe_gallery}/"
            f"{safe_ref}{suffix}"
        )

        storage_location = "spaces://" + object_key

        if not exists(
            settings,
            storage_location,
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Direct preview upload has not "
                    "reached storage"
                ),
            )

        with request.app.state.session_factory() as session:
            gallery = session.scalar(
                select(Gallery).where(
                    Gallery.source_ref == gallery_ref
                )
            )

            if gallery is None:
                raise HTTPException(
                    status_code=404,
                    detail="Gallery source reference not found",
                )

            asset = session.scalar(
                select(GalleryAsset).where(
                    GalleryAsset.gallery_id
                    == gallery.id,
                    GalleryAsset.source_ref
                    == asset_ref,
                )
            )

            if asset is None:
                asset = GalleryAsset(
                    gallery_id=gallery.id,
                    source_ref=asset_ref,
                    filename=(
                        x_pirouette_filename
                        or f"{safe_ref}{suffix}"
                    ),
                    storage_path=storage_location,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    folder_path=folder_path,
                    media_kind=media_kind,
                    status="ready",
                )
                session.add(asset)
            else:
                asset.filename = (
                    x_pirouette_filename
                    or f"{safe_ref}{suffix}"
                )
                asset.storage_path = storage_location
                asset.content_type = content_type
                asset.size_bytes = size_bytes
                asset.folder_path = folder_path
                asset.media_kind = media_kind
                asset.status = "ready"

            session.commit()
            session.refresh(asset)

        return {
            "id": asset.id,
            "source_ref": asset.source_ref,
            "filename": asset.filename,
            "folder_path": asset.folder_path,
            "media_kind": asset.media_kind,
            "size_bytes": int(asset.size_bytes or 0),
            "status": asset.status,
            "public_preview_available": bool(asset.storage_path),
            "storage": "spaces",
        }


    @router.put("/galleries/{gallery_ref}/assets/{asset_ref}")
    async def upload_gallery_asset(
        gallery_ref: str,
        asset_ref: str,
        request: Request,
        file: UploadFile = File(...),
        x_pirouette_sync_key: str | None = Header(default=None),
        x_pirouette_folder_path: str | None = Header(default=""),
        x_pirouette_media_kind: str | None = Header(default="photos"),
    ):
        _authorise(request, x_pirouette_sync_key)
        folder_path = "/".join(part for part in unquote(x_pirouette_folder_path or "").replace("\\", "/").strip("/").split("/") if part not in ("", ".", ".."))
        media_kind = "videos" if (x_pirouette_media_kind or "").lower() == "videos" else "photos"
        safe_ref = re.sub(r"[^A-Za-z0-9._-]+", "-", asset_ref).strip("-") or "asset"
        content = await file.read(request.app.state.settings.max_live_asset_bytes + 1)
        if len(content) > request.app.state.settings.max_live_asset_bytes:
            raise HTTPException(status_code=413, detail="Asset exceeds the configured live-gallery limit")
        settings = request.app.state.settings
        suffix = Path(file.filename or "image.jpg").suffix.lower() or ".jpg"

        if spaces_enabled(settings):
            safe_gallery = re.sub(
                r"[^A-Za-z0-9._-]+",
                "-",
                gallery_ref,
            ).strip("-") or "gallery"

            object_key = (
                f"gallery-previews/"
                f"{safe_gallery}/"
                f"{safe_ref}{suffix}"
            )

            storage_location = upload_bytes(
                settings,
                object_key,
                content,
                file.content_type or "image/jpeg",
            )
            target = None
        else:
            root = Path(settings.temporary_media_dir).expanduser().resolve()
            target_dir = root / re.sub(
                r"[^A-Za-z0-9._-]+",
                "-",
                gallery_ref,
            )
            target_dir.mkdir(parents=True, exist_ok=True)

            target = target_dir / f"{safe_ref}{suffix}"
            temp = target.with_suffix(target.suffix + ".part")
            temp.write_bytes(content)
            os.replace(temp, target)
            storage_location = str(target)

        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == gallery_ref))
            if gallery is None:
                if target is not None:
                    target.unlink(missing_ok=True)
                elif storage_location.startswith("spaces://"):
                    delete(settings, storage_location)

                raise HTTPException(
                    status_code=404,
                    detail="Gallery source reference not found",
                )
            asset = session.scalar(select(GalleryAsset).where(GalleryAsset.gallery_id == gallery.id, GalleryAsset.source_ref == asset_ref))
            if asset is None:
                asset = GalleryAsset(
                    gallery_id=gallery.id,
                    source_ref=asset_ref,
                    filename=file.filename or f"{safe_ref}{suffix}",
                    storage_path=storage_location,
                    content_type=file.content_type or "image/jpeg",
                    size_bytes=len(content),
                    folder_path=folder_path,
                    media_kind=media_kind,
                    status="ready",
                )
                session.add(asset)
            else:
                asset.filename = file.filename or f"{safe_ref}{suffix}"
                asset.storage_path = storage_location
                asset.content_type = file.content_type or "image/jpeg"
                asset.size_bytes = len(content)
                asset.folder_path = folder_path
                asset.media_kind = media_kind
                asset.status = "ready"
            session.commit()
            session.refresh(asset)
        return {"id": asset.id, "source_ref": asset.source_ref, "size_bytes": asset.size_bytes, "status": asset.status}

    @router.put("/galleries/{gallery_ref}/delivery-assets/{asset_ref}")
    async def upload_gallery_delivery_asset(
        gallery_ref: str, asset_ref: str, request: Request, file: UploadFile = File(...),
        x_pirouette_sync_key: str | None = Header(default=None),
        x_pirouette_delivery_type: str | None = Header(default="low_res"),
    ):
        """Store a private purchased file only after a committed paid order.

        Public gallery sync never sends these files. Low-resolution and original
        high-resolution delivery sources are kept separately and are never served
        from the public gallery route.
        """
        _authorise(request, x_pirouette_sync_key)
        content = await file.read(request.app.state.settings.max_delivery_asset_bytes + 1)
        if len(content) > request.app.state.settings.max_delivery_asset_bytes:
            raise HTTPException(status_code=413, detail="Delivery asset exceeds the configured limit")
        settings = request.app.state.settings
        root = Path(settings.temporary_media_dir).expanduser().resolve()
        delivery_type = "high_res" if (x_pirouette_delivery_type or "").lower() == "high_res" else "low_res"
        safe_gallery = re.sub(r"[^A-Za-z0-9._-]+", "-", gallery_ref)
        target_dir = root / safe_gallery / "delivery" / delivery_type
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_ref = re.sub(r"[^A-Za-z0-9._-]+", "-", asset_ref).strip("-") or "asset"
        suffix = Path(file.filename or "image.jpg").suffix.lower() or ".jpg"
        target = target_dir / f"{safe_ref}{suffix}"

        if spaces_enabled(settings):
            object_key = f"delivery-assets/{safe_gallery}/{delivery_type}/{safe_ref}{suffix}"

            logger.warning(
                "DELIVERY_UPLOAD_START gallery=%s asset=%s type=%s bytes=%s key=%s",
                gallery_ref,
                asset_ref,
                delivery_type,
                len(content),
                object_key,
            )

            storage_location = upload_bytes(
                settings,
                object_key,
                content,
                file.content_type or "image/jpeg",
            )

            logger.warning(
                "DELIVERY_UPLOAD_SPACES_OK gallery=%s asset=%s location=%s",
                gallery_ref,
                asset_ref,
                storage_location,
            )
        else:
            temp = target.with_suffix(target.suffix + ".part")
            temp.write_bytes(content)
            os.replace(temp, target)
            storage_location = str(target)

        logger.warning(
            "DELIVERY_UPLOAD_DB_START gallery=%s asset=%s",
            gallery_ref,
            asset_ref,
        )

        with request.app.state.session_factory() as session:
            gallery = session.scalar(select(Gallery).where(Gallery.source_ref == gallery_ref))
            if gallery is None:
                if not spaces_enabled(settings):
                    target.unlink(missing_ok=True)
                raise HTTPException(status_code=404, detail="Gallery source reference not found")
            asset = session.scalar(select(GalleryAsset).where(GalleryAsset.gallery_id == gallery.id, GalleryAsset.source_ref == asset_ref))
            if asset is None:
                if not spaces_enabled(settings):
                    target.unlink(missing_ok=True)
                raise HTTPException(status_code=404, detail="Public gallery asset must be mirrored first")
            if delivery_type == "high_res":
                asset.highres_delivery_storage_path = storage_location
            else:
                asset.delivery_storage_path = storage_location
            session.commit()

        logger.warning(
            "DELIVERY_UPLOAD_COMPLETE gallery=%s asset=%s type=%s",
            gallery_ref,
            asset_ref,
            delivery_type,
        )

        return {"source_ref": asset_ref, "delivery_asset": "ready", "delivery_type": delivery_type, "size_bytes": len(content)}


    @router.post("/event-availability-requests")
    async def create_event_availability_request(
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _authorise(request, x_pirouette_sync_key)
        from app.availability_requests import create_remote_request
        payload = await request.json()
        with request.app.state.session_factory() as session:
            try:
                return create_remote_request(
                    session,
                    request.app.state.settings,
                    source_ref=str(payload.get("source_ref") or ""),
                    event_source_ref=str(payload.get("event_source_ref") or ""),
                    event_name=str(payload.get("event_name") or ""),
                    venue=str(payload.get("venue") or ""),
                    start_date=date.fromisoformat(str(payload.get("start_date") or "")),
                    end_date=date.fromisoformat(str(payload.get("end_date") or payload.get("start_date") or "")),
                    lock_date=date.fromisoformat(str(payload.get("lock_date") or "")),
                    staff_source_ref=str(payload.get("staff_source_ref") or ""),
                    staff_name=str(payload.get("staff_name") or ""),
                    staff_email=str(payload.get("staff_email") or ""),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/event-availability-requests/{source_ref}")
    async def get_event_availability_request(
        source_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _authorise(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            row = session.scalar(select(EventAvailabilityRequest).where(EventAvailabilityRequest.source_ref == source_ref))
            if row is None:
                raise HTTPException(status_code=404, detail="Availability request not found")
            now = datetime.now(timezone.utc)
            expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
            if row.status == "sent" and expires <= now:
                row.status = "expired"
                row.token_hash = None
                session.commit()
            return {
                "source_ref": row.source_ref,
                "status": row.status,
                "availability_status": row.availability_status,
                "response_notes": row.response_notes,
                "emailed_at": row.emailed_at.isoformat() if row.emailed_at else None,
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                "lock_date": row.lock_date.isoformat(),
            }



    @router.post("/staff-payroll-submissions")
    async def upsert_staff_payroll_submission(
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Create/update one staff member's Wednesday-Tuesday payroll draft."""
        _authorise(request, x_pirouette_sync_key)

        import json

        payload = await request.json()

        source_ref = str(payload.get("source_ref") or "").strip()
        staff_source_ref = str(payload.get("staff_source_ref") or "").strip()
        staff_name = str(payload.get("staff_name") or "").strip()
        staff_email = str(payload.get("staff_email") or "").strip()
        week_start_raw = str(payload.get("week_start") or "").strip()
        payroll_payload = payload.get("payload") or {}

        if not source_ref:
            raise HTTPException(status_code=400, detail="source_ref is required")
        if not staff_source_ref:
            raise HTTPException(status_code=400, detail="staff_source_ref is required")
        if not staff_email:
            raise HTTPException(status_code=400, detail="staff_email is required")

        try:
            week_start = date.fromisoformat(week_start_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="week_start is invalid") from exc

        if week_start.weekday() != 2:
            raise HTTPException(
                status_code=400,
                detail="Payroll week must begin on a Wednesday",
            )

        if not isinstance(payroll_payload, dict):
            raise HTTPException(status_code=400, detail="payload must be an object")

        now = datetime.now(timezone.utc)

        # Allow the link to remain valid long enough for the weekly cycle,
        # but the public payroll form will enforce submission state separately.
        expires_at = now + timedelta(days=21)

        with request.app.state.session_factory() as session:
            row = session.scalar(
                select(StaffPayrollSubmission).where(
                    StaffPayrollSubmission.source_ref == source_ref
                )
            )

            if row is None:
                row = StaffPayrollSubmission(
                    source_ref=source_ref,
                    staff_source_ref=staff_source_ref,
                    staff_name=staff_name[:200],
                    staff_email=staff_email[:320],
                    week_start=week_start,
                    status="draft",
                    payload_json="{}",
                    expires_at=expires_at,
                )
                session.add(row)

            # A submitted payroll week is immutable through ordinary sync.
            if row.status == "submitted":
                return {
                    "source_ref": row.source_ref,
                    "status": row.status,
                    "week_start": row.week_start.isoformat(),
                    "submitted_at": (
                        row.submitted_at.isoformat()
                        if row.submitted_at else None
                    ),
                    "locked": True,
                }

            # Once a payroll week has received an hourly-rate snapshot,
            # preserve that rate for the life of the week. Hours and expenses
            # may continue to change while the week is a draft, but a later
            # staff pay-rate edit must not rewrite historical payroll.
            try:
                existing_payload = json.loads(row.payload_json or "{}")
            except Exception:
                existing_payload = {}

            if (
                isinstance(existing_payload, dict)
                and "hourly_rate" in existing_payload
            ):
                payroll_payload["hourly_rate"] = existing_payload["hourly_rate"]

            row.staff_source_ref = staff_source_ref
            row.staff_name = staff_name[:200]
            row.staff_email = staff_email[:320]
            row.week_start = week_start
            row.payload_json = json.dumps(
                payroll_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            row.expires_at = expires_at
            row.updated_at = now

            session.commit()

            return {
                "source_ref": row.source_ref,
                "status": row.status,
                "week_start": row.week_start.isoformat(),
                "emailed_at": (
                    row.emailed_at.isoformat()
                    if row.emailed_at else None
                ),
                "submitted_at": (
                    row.submitted_at.isoformat()
                    if row.submitted_at else None
                ),
                "locked": row.status == "submitted",
            }



    @router.post("/staff-payroll-submissions/{source_ref}/email")
    async def email_staff_payroll_submission(
        source_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _authorise(request, x_pirouette_sync_key)

        from app.payroll_requests import issue_secure_link

        with request.app.state.session_factory() as session:
            row = session.scalar(
                select(StaffPayrollSubmission).where(
                    StaffPayrollSubmission.source_ref == source_ref
                )
            )

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Staff payroll submission not found",
                )

            return issue_secure_link(
                session,
                request.app.state.settings,
                row,
            )


    @router.get("/staff-payroll-submissions/{source_ref}")
    async def get_staff_payroll_submission(
        source_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Read Cloud payroll status through the authenticated sync channel."""
        _authorise(request, x_pirouette_sync_key)

        import json

        with request.app.state.session_factory() as session:
            row = session.scalar(
                select(StaffPayrollSubmission).where(
                    StaffPayrollSubmission.source_ref == source_ref
                )
            )

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Staff payroll submission not found",
                )

            try:
                payroll_payload = json.loads(row.payload_json or "{}")
            except Exception:
                payroll_payload = {}

            return {
                "source_ref": row.source_ref,
                "staff_source_ref": row.staff_source_ref,
                "staff_name": row.staff_name,
                "staff_email": row.staff_email,
                "week_start": row.week_start.isoformat(),
                "status": row.status,
                "payload": payroll_payload,
                "emailed_at": (
                    row.emailed_at.isoformat()
                    if row.emailed_at else None
                ),
                "submitted_at": (
                    row.submitted_at.isoformat()
                    if row.submitted_at else None
                ),
                "expires_at": (
                    row.expires_at.isoformat()
                    if row.expires_at else None
                ),
            }


    @router.post("/staff-profile-requests")
    async def create_staff_profile_request(
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _authorise(request, x_pirouette_sync_key)

        from app.profile_requests import create_remote_request

        payload = await request.json()

        with request.app.state.session_factory() as session:
            return create_remote_request(
                session,
                request.app.state.settings,
                source_ref=str(payload.get("source_ref") or ""),
                staff_code=str(payload.get("staff_code") or ""),
                staff_name=str(payload.get("staff_name") or ""),
                preferred_name=str(payload.get("preferred_name") or ""),
                role=str(payload.get("role") or ""),
                staff_email=str(payload.get("staff_email") or ""),
            )


    @router.get("/staff-profile-requests/{source_ref}")
    async def get_staff_profile_request(
        source_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _authorise(request, x_pirouette_sync_key)

        import json
        from datetime import datetime, timezone
        from sqlalchemy import select
        from app.db.models import StaffProfileRequest

        with request.app.state.session_factory() as session:
            row = session.scalar(
                select(StaffProfileRequest).where(
                    StaffProfileRequest.source_ref == source_ref
                )
            )

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Staff profile request not found.",
                )

            now = datetime.now(timezone.utc)
            expires = row.expires_at

            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)

            if row.status == "sent" and expires <= now:
                row.status = "expired"
                row.token_hash = None
                session.commit()

            profile = {}

            if row.status == "submitted" and row.payload_json:
                try:
                    profile = json.loads(row.payload_json)
                except Exception:
                    profile = {}

            return {
                "source_ref": row.source_ref,
                "staff_code": row.staff_code,
                "status": row.status,
                "emailed_at": (
                    row.emailed_at.isoformat()
                    if row.emailed_at else None
                ),
                "expires_at": (
                    row.expires_at.isoformat()
                    if row.expires_at else None
                ),
                "submitted_at": (
                    row.submitted_at.isoformat()
                    if row.submitted_at else None
                ),
                "profile": profile,
                "photo_available": bool(
                    row.status == "submitted"
                    and row.photo_storage_path
                ),
                "photo_filename": row.photo_filename,
                "photo_content_type": row.photo_content_type,
            }


    @router.get("/staff-profile-requests/{source_ref}/photo")
    async def get_staff_profile_request_photo(
        source_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _authorise(request, x_pirouette_sync_key)

        from fastapi.responses import Response
        from sqlalchemy import select
        from app.db.models import StaffProfileRequest
        from app.storage import download_to_temp

        with request.app.state.session_factory() as session:
            row = session.scalar(
                select(StaffProfileRequest).where(
                    StaffProfileRequest.source_ref == source_ref
                )
            )

            if (
                row is None
                or row.status != "submitted"
                or not row.photo_storage_path
            ):
                raise HTTPException(
                    status_code=404,
                    detail="Submitted staff photograph not found.",
                )

            suffix = Path(
                row.photo_filename or ""
            ).suffix

            temp = download_to_temp(
                request.app.state.settings,
                row.photo_storage_path,
                suffix=suffix,
            )

            try:
                content = temp.read_bytes()
            finally:
                if str(row.photo_storage_path).startswith("spaces://"):
                    temp.unlink(missing_ok=True)

            return Response(
                content=content,
                media_type=(
                    row.photo_content_type
                    or "application/octet-stream"
                ),
                headers={
                    "Cache-Control": "no-store",
                    "X-Pirouette-Filename": (
                        row.photo_filename
                        or "staff-photo.jpg"
                    ),
                },
            )


    @router.delete("/staff-profile-requests/{source_ref}")
    async def delete_staff_profile_request(
        source_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _authorise(request, x_pirouette_sync_key)

        from sqlalchemy import select
        from app.db.models import StaffProfileRequest
        from app.profile_requests import purge_remote_request

        with request.app.state.session_factory() as session:
            row = session.scalar(
                select(StaffProfileRequest).where(
                    StaffProfileRequest.source_ref == source_ref
                )
            )

            if row is None:
                return {
                    "ok": True,
                    "deleted": False,
                    "source_ref": source_ref,
                }

            purge_remote_request(
                session,
                request.app.state.settings,
                row,
            )

        return {
            "ok": True,
            "deleted": True,
            "source_ref": source_ref,
        }


    @router.get("/diagnostics/runtime")
    async def runtime_diagnostics(
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        """Temporary authenticated runtime fingerprint for deployment diagnosis."""
        _authorise(request, x_pirouette_sync_key)

        import socket
        from sqlalchemy import text as sql_text

        settings = request.app.state.settings

        with request.app.state.session_factory() as session:
            row = session.execute(
                sql_text(
                    """
                    SELECT
                        current_database(),
                        inet_server_addr()::text,
                        inet_server_port(),
                        pg_backend_pid()
                    """
                )
            ).one()

        return {
            "container_hostname": socket.gethostname(),
            "database_name": row[0],
            "database_server_addr": row[1],
            "database_server_port": row[2],
            "database_backend_pid": row[3],
            "environment": settings.environment,
            "version": settings.version,
        }


    return router
