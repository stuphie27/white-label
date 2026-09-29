from __future__ import annotations

from datetime import date, datetime, time, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, String, Text, Time, UniqueConstraint, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True, index=True)
    master_source_ref: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        unique=True,
        index=True,
    )
    client_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    principal_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    photographer_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    dance_style: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    brand_id: Mapped[str] = mapped_column(String(80), nullable=False, default="sophies", index=True)
    personal_video_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    personal_video_type: Mapped[str] = mapped_column(String(30), nullable=False, default="")

    # Stuphie Online Event Master
    promoter_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    promoter_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    promoter_phone: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    staff_required: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    event_end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    travel_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hotel_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hotel_info: Mapped[str] = mapped_column(Text, nullable=False, default="")
    planning_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    photography_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    video_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    printing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    customer_kiosk_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    event_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    package_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    package_state: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="not_published",
    )
    package_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    package_published_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    arrival_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    photography_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    photography_finish: Mapped[time | None] = mapped_column(Time, nullable=True)
    venue: Mapped[str] = mapped_column(String(250), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_logo_storage_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    gallery_access_code_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    booking_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contract_received: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    photographer_assigned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    equipment_packed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    galleries_uploaded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gallery_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    orders_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    service_desk_payments_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_collection_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mirrored_customers_browsing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mirrored_active_favourites: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mirrored_active_baskets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mirrored_orders_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mirrored_last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pricing_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Gallery(Base):
    __tablename__ = "galleries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True, index=True)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    access_code_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class TransferJob(Base):
    __tablename__ = "transfer_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True, index=True)
    gallery_id: Mapped[str] = mapped_column(String(36), ForeignKey("galleries.id", ondelete="CASCADE"), nullable=False, index=True)
    destination: Mapped[str] = mapped_column(String(20), nullable=False, default="customer", index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="waiting", index=True)
    item_count: Mapped[int] = mapped_column(nullable=False, default=0)
    bytes_total: Mapped[int] = mapped_column(nullable=False, default=0)
    bytes_transferred: Mapped[int] = mapped_column(nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SyncAsset(Base):
    __tablename__ = "sync_assets"
    __table_args__ = (UniqueConstraint("transfer_job_id", "source_ref", name="uq_sync_asset_job_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    transfer_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("transfer_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    bytes_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="waiting", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class GalleryFolder(Base):
    __tablename__ = "gallery_folders"
    __table_args__ = (UniqueConstraint("gallery_id", "media_kind", "path", name="uq_gallery_folder_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    gallery_id: Mapped[str] = mapped_column(String(36), ForeignKey("galleries.id", ondelete="CASCADE"), nullable=False, index=True)
    media_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="photos", index=True)
    path: Mapped[str] = mapped_column(String(800), nullable=False, default="")
    parent_path: Mapped[str] = mapped_column(String(800), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class GalleryAsset(Base):
    __tablename__ = "gallery_assets"
    __table_args__ = (UniqueConstraint("gallery_id", "source_ref", name="uq_gallery_asset_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    gallery_id: Mapped[str] = mapped_column(String(36), ForeignKey("galleries.id", ondelete="CASCADE"), nullable=False, index=True)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_storage_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    highres_delivery_storage_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    folder_path: Mapped[str] = mapped_column(String(800), nullable=False, default="", index=True)
    media_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="photos", index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CustomerFavouriteSession(Base):
    __tablename__ = "customer_favourite_sessions"
    __table_args__ = (UniqueConstraint("gallery_id", "email", name="uq_cloud_favourite_gallery_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    gallery_id: Mapped[str] = mapped_column(String(36), ForeignKey("galleries.id", ondelete="CASCADE"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    retention_choice: Mapped[str] = mapped_column(String(30), nullable=False, default="temporary_link")
    extension_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extension_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CustomerFavourite(Base):
    __tablename__ = "customer_favourites"
    __table_args__ = (UniqueConstraint("session_id", "asset_id", name="uq_cloud_favourite_session_asset"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer_favourite_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("gallery_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class CustomerDelivery(Base):
    __tablename__ = "customer_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True, index=True)
    event_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    order_reference: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    customer_phone: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    delivery_type: Mapped[str] = mapped_column(String(20), nullable=False, default="low_res")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    reminder_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    zip_path: Mapped[str] = mapped_column(Text, nullable=False)
    item_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ready", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reminder_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_downloads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 = unlimited until expiry
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CloudOrder(Base):
    __tablename__ = "cloud_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True, index=True)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    customer_phone: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    customer_at_event: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    product_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False)
    fulfilment_method: Mapped[str] = mapped_column(String(40), nullable=False)
    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    address_line_2: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    town: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    county: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    postcode: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(80), nullable=False, default="United Kingdom")
    delivery_charge_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_reference: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    payment_status: Mapped[str] = mapped_column(String(30), nullable=False, default="awaiting_payment", index=True)
    payment_amount_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sumup_checkout_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    sumup_checkout_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sumup_checkout_reference: Mapped[str] = mapped_column(String(90), nullable=False, default="")
    sumup_status: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    sumup_transaction_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    payment_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order_items_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    delivery_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    fulfilment_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="awaiting_payment", index=True)
    staff_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class OrderAudit(Base):
    __tablename__ = "order_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("cloud_orders.id", ondelete="CASCADE"), nullable=True, index=True)
    order_reference: Mapped[str] = mapped_column(String(120), nullable=False, default="", index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    system: Mapped[str] = mapped_column(String(40), nullable=False, default="cloud")
    old_status: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    new_status: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


class EventAvailabilityRequest(Base):
    __tablename__ = "event_availability_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_ref: Mapped[str] = mapped_column(String(220), nullable=False, unique=True, index=True)
    event_source_ref: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(220), nullable=False, default="")
    venue: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    lock_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    staff_source_ref: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    staff_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    staff_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="sent", index=True)
    availability_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    response_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))



class StaffPayrollSubmission(Base):
    __tablename__ = "staff_payroll_submissions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    source_ref: Mapped[str] = mapped_column(
        String(220),
        nullable=False,
        unique=True,
        index=True,
    )

    staff_source_ref: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )

    staff_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    staff_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )

    week_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="draft",
        index=True,
    )

    payload_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
    )

    emailed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    approved_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    paid_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    payment_reference: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StaffPayrollLoginToken(Base):
    """Single-use magic-link challenge for employee payroll history."""

    __tablename__ = "staff_payroll_login_tokens"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    staff_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class StaffProfileRequest(Base):
    __tablename__ = "staff_profile_requests"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    source_ref: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        index=True,
    )

    staff_code: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="",
    )

    staff_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    preferred_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    role: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="",
    )

    staff_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="sent",
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    emailed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    payload_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    photo_storage_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    photo_filename: Mapped[str] = mapped_column(
        String(240),
        nullable=False,
        default="",
    )

    photo_content_type: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

class PersonalVideoBooking(Base):
    __tablename__ = "personal_video_bookings"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    source_ref: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        unique=True,
        index=True,
    )
    event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reference: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        unique=True,
        index=True,
    )
    manage_token: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        unique=True,
        index=True,
    )
    booking_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    customer_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    customer_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )
    customer_phone: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="",
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="new",
        index=True,
    )
    payment_status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="awaiting_payment",
        index=True,
    )

    initial_payment_pence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    total_paid_pence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    final_total_pence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    customer_notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    sumup_checkout_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )
    payment_method: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="",
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    competitor_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    outfit_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    identification_photo_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    minimum_video_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    video_format: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="",
    )
    intended_use: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    terms_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class PersonalVideoEntry(Base):
    __tablename__ = "personal_video_entries"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    source_ref: Mapped[str | None] = mapped_column(
        String(220),
        nullable=True,
        unique=True,
        index=True,
    )
    booking_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "personal_video_bookings.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    dancer_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )
    dancer_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    dance_style: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )
    category: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )
    age_group: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="",
    )

    event_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    event_name: Mapped[str] = mapped_column(
        String(240),
        nullable=False,
        default="",
    )
    dance_name: Mapped[str] = mapped_column(
        String(240),
        nullable=False,
        default="",
    )

    performance_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    performance_time: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )

    expected_video_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    filming_instruction: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    filming_details: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    unit_price_pence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="new",
        index=True,
    )
    external_video_link: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    link_ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    outfit_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    identification_photo_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StaffPasswordReset(Base):
    """
    One-time staff password-reset credential.

    The raw reset token is never stored. Only its SHA-256 digest is
    persisted. Tokens expire and become unusable after successful use.
    """

    __tablename__ = "staff_password_resets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    staff_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )

    token_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class StaffMember(Base):
    """
    Permanent Stuphie staff identity.

    staff_source_ref is the common identity used by the existing
    staff profile, availability and payroll workflows.
    """

    __tablename__ = "staff_members"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    staff_source_ref: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        index=True,
    )

    staff_code: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="",
        index=True,
    )

    staff_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    preferred_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
        index=True,
    )

    default_role: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="Photography Staff",
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    # Staff Hub directory-safe contact information.
    telephone: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="",
    )

    # Private employee information. This is NEVER directory data.
    # Access is limited to the employee and authorised management.
    private_profile_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    # Private staff photograph stored in DigitalOcean Spaces.
    photo_storage_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    photo_filename: Mapped[str] = mapped_column(
        String(240),
        nullable=False,
        default="",
    )

    photo_content_type: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )

    profile_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


    # Online staff authentication.
    #
    # Existing staff accounts remain unable to use password login until
    # login_enabled is explicitly activated. This allows the authentication
    # migration to be introduced without changing the current Super Admin
    # login journey.
    password_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    login_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StaffPaymentProfile(Base):
    """
    Private payment arrangement for a StaffMember.

    This is Payroll Admin data only. It must never be exposed
    through the ordinary Staff Hub or staff directory.

    payment_arrangement values:
      self_employed_contractor
      external_payroll
    """

    __tablename__ = "staff_payment_profiles"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    staff_member_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "staff_members.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    payment_arrangement: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="self_employed_contractor",
        index=True,
    )

    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    updated_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StaffPayRate(Base):
    """
    Effective-dated private hourly rate history.

    Values are stored as integer pence to avoid floating-point
    money errors. Historic records remain intact when a future
    rate is introduced.
    """

    __tablename__ = "staff_pay_rates"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    staff_member_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "staff_members.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    hourly_rate_pence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    created_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )



class EventStaffAssignment(Base):
    """
    Assignment of one StaffMember to one Event.

    This is operational staffing only. Payroll remains independent.
    """

    __tablename__ = "event_staff_assignments"

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "staff_member_id",
            name="uq_event_staff_assignment",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    staff_member_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("staff_members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="Photography Staff",
    )

    assignment_status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="assigned",
        index=True,
    )

    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class EventStaffShift(Base):
    """
    Operational working shift for one staff assignment at one event.

    This records scheduled and actual attendance.
    Payroll remains independent and is not written by this model.
    """

    __tablename__ = "event_staff_shifts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    assignment_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "event_staff_assignments.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    staff_member_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("staff_members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    shift_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="Photography Staff",
    )

    scheduled_start: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )

    scheduled_finish: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )

    actual_clock_in: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    actual_clock_out: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    break_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="scheduled",
        index=True,
    )

    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    manually_corrected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    corrected_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    corrected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    approved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    approved_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StaffAdditionalWork(Base):
    """
    Staff-submitted work completed outside an EventStaffShift.

    Examples include editing, admin, preparation, post-production,
    equipment work and other authorised business activity.

    Records remain separate from EventStaffShift so an employee may
    legitimately have event work and additional work on the same date.
    Only approved records are eligible for Payroll handoff.
    """

    __tablename__ = "staff_additional_work"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    staff_member_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("staff_members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    event_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    work_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    work_type: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="Additional Work",
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    start_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )

    finish_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )

    break_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="submitted",
        index=True,
    )

    approved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    approved_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    returned_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    returned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    return_notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StaffDocument(Base):
    """
    Private Staff Hub document metadata.

    Actual file contents remain private in DigitalOcean Spaces.
    """

    __tablename__ = "staff_documents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    title: Mapped[str] = mapped_column(
        String(240),
        nullable=False,
        default="",
        index=True,
    )

    category: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="Staff Guidance",
        index=True,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="1.0",
    )

    storage_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    filename: Mapped[str] = mapped_column(
        String(240),
        nullable=False,
        default="",
    )

    content_type: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="application/octet-stream",
    )

    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    requires_acknowledgement: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    uploaded_by: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StaffDocumentView(Base):
    """
    Audit record created whenever an authenticated staff member
    opens a Staff Hub document.
    """

    __tablename__ = "staff_document_views"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "staff_documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    staff_member_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "staff_members.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    staff_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
        index=True,
    )

    staff_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class StaffDocumentAcknowledgement(Base):
    """
    Explicit staff acknowledgement of a policy/document.

    Opening a document and acknowledging it are deliberately
    recorded separately.
    """

    __tablename__ = "staff_document_acknowledgements"

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "staff_member_id",
            name="uq_staff_document_ack_document_staff",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "staff_documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    staff_member_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "staff_members.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    staff_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
        index=True,
    )

    document_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="",
    )

    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class StaffEventInterest(Base):
    """
    Employee expression of interest in working at an Event Master event.

    Interest is deliberately separate from assignment and availability.
    Management still decides who is actually assigned.
    """

    __tablename__ = "staff_event_interests"

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "staff_member_id",
            name="uq_staff_event_interest_event_staff",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    staff_member_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("staff_members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="interested",
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

# ---------------------------------------------------------------------------
# STUPHIE verified customer identity
# ---------------------------------------------------------------------------

class CustomerIdentity(Base):
    """Verified Sophie’s Photography customer identity.

    Email is the canonical cross-device identity key. A customer must prove
    ownership of the address through a short-lived verification challenge
    before this identity is placed into the signed customer web session.
    """

    __tablename__ = "customer_identities"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )
    last_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CustomerConsent(Base):
    """Versioned consent recorded for a verified customer identity."""

    __tablename__ = "customer_consents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    customer_identity_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    terms_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    privacy_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    copyright_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class CustomerVerificationChallenge(Base):
    """Single-use six-digit customer email verification challenge.

    Raw verification codes are deliberately never stored.
    """

    __tablename__ = "customer_verification_challenges"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )
    last_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )
    code_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


# ---------------------------------------------------------------------------
# STUPHIE CUSTOMER LIVE ACTIVITY
# ---------------------------------------------------------------------------

class CustomerGalleryActivity(Base):
    """Current and cumulative activity for a verified online customer."""

    __tablename__ = "customer_gallery_activity"
    __table_args__ = (
        UniqueConstraint(
            "gallery_id",
            "customer_identity_id",
            name="uq_customer_gallery_activity_identity",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    gallery_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("galleries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    customer_identity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("customer_identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    customer_name: Mapped[str] = mapped_column(
        String(250),
        nullable=False,
        default="",
    )

    customer_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        default="",
        index=True,
    )

    current_area: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="gallery",
    )

    current_folder: Mapped[str] = mapped_column(
        String(800),
        nullable=False,
        default="",
    )

    photo_views: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    folder_views: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    favourite_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    basket_photo_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# SOPHIE'S REWARDS - READ-ONLY CLOUD SNAPSHOT
# ---------------------------------------------------------------------------

class CustomerRewardSnapshot(Base):
    """Offline-authoritative Sophie’s Rewards balance mirrored to Cloud."""

    __tablename__ = "customer_reward_snapshots"

    email: Mapped[str] = mapped_column(
        String(320),
        primary_key=True,
    )

    points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
