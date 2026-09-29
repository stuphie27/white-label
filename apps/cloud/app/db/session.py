from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

LOGGER = logging.getLogger("pirouette.database")


def build_engine(database_url: str) -> Engine:
    kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": 5},
    }
    if database_url == "sqlite+pysqlite:///:memory:":
        kwargs = {
            "pool_pre_ping": True,
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    elif database_url.startswith("sqlite"):
        kwargs = {
            "pool_pre_ping": True,
            "connect_args": {"check_same_thread": False},
        }
    else:
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=300)
    return create_engine(database_url, **kwargs)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def database_is_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        LOGGER.warning("Database readiness check failed: %s", exc)
        return False


def database_schema_is_ready(engine: Engine) -> bool:
    """Return True only when the tables required by this release exist.

    A reachable database is not necessarily initialised. Keeping this check
    separate prevents the staff dashboard from querying tables that have not
    been migrated yet.
    """
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        if "staff_profile_requests" not in tables or "event_availability_requests" not in tables:
            return False
        if not {
            "personal_video_bookings",
            "personal_video_entries",
        }.issubset(tables):
            return False
        if not {"schema_migrations", "events", "galleries", "transfer_jobs", "sync_assets", "gallery_assets", "gallery_folders", "customer_deliveries"}.issubset(tables):
            return False
        event_columns = {column["name"] for column in inspector.get_columns("events")}
        required_columns = {
            "id", "source_ref", "name", "client_name", "principal_name", "photographer_name",
            "dance_style", "start_date", "end_date", "arrival_time",
            "photography_start", "photography_finish", "venue", "description",
            "internal_notes", "status", "booking_confirmed", "contract_received",
            "photographer_assigned", "equipment_packed", "galleries_uploaded",
            "gallery_published", "orders_complete", "personal_video_enabled", "personal_video_type", "created_at", "updated_at",
        }
        gallery_columns = {column["name"] for column in inspector.get_columns("galleries")}
        required_gallery_columns = {
            "id", "source_ref", "event_id", "name", "slug", "status", "visibility",
            "access_code_hash", "expires_at", "description", "created_at", "updated_at",
        }
        transfer_columns = {column["name"] for column in inspector.get_columns("transfer_jobs")}
        required_transfer_columns = {
            "id", "source_ref", "gallery_id", "destination", "status", "item_count",
            "bytes_total", "bytes_transferred", "expires_at", "completed_at",
            "error_message", "created_at", "updated_at",
        }
        sync_asset_columns = {column["name"] for column in inspector.get_columns("sync_assets")}
        required_sync_asset_columns = {
            "id", "transfer_job_id", "source_ref", "filename", "size_bytes",
            "sha256", "bytes_received", "status", "created_at", "updated_at",
        }
        gallery_asset_columns = {column["name"] for column in inspector.get_columns("gallery_assets")}
        required_gallery_asset_columns = {
            "id", "gallery_id", "source_ref", "filename", "storage_path", "delivery_storage_path",
            "content_type", "size_bytes", "folder_path", "media_kind", "status", "created_at", "updated_at",
        }
        return (
            required_columns.issubset(event_columns)
            and required_gallery_columns.issubset(gallery_columns)
            and required_transfer_columns.issubset(transfer_columns)
            and required_sync_asset_columns.issubset(sync_asset_columns)
            and required_gallery_asset_columns.issubset(gallery_asset_columns)
        )
    except Exception as exc:
        LOGGER.warning("Database schema check failed: %s", exc)
        return False
