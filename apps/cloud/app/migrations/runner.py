from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import Column, DateTime, MetaData, String, Table, inspect, select, text
from sqlalchemy.engine import Connection, Engine

from app.db.models import Base

LOGGER = logging.getLogger("pirouette.migrations")
MIGRATIONS: list[tuple[str, Callable[[Connection], None]]] = []


def migration(version: str):
    def decorator(function: Callable[[Connection], None]):
        MIGRATIONS.append((version, function))
        return function
    return decorator


@migration("001_create_events")
def create_events(connection: Connection) -> None:
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["events"]])


@migration("002_event_operations_fields")
def add_event_operations_fields(connection: Connection) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns("events")}
    statements = {
        "client_name": "ALTER TABLE events ADD COLUMN client_name VARCHAR(200) NOT NULL DEFAULT ''",
        "principal_name": "ALTER TABLE events ADD COLUMN principal_name VARCHAR(200) NOT NULL DEFAULT ''",
        "photographer_name": "ALTER TABLE events ADD COLUMN photographer_name VARCHAR(200) NOT NULL DEFAULT ''",
        "dance_style": "ALTER TABLE events ADD COLUMN dance_style VARCHAR(120) NOT NULL DEFAULT ''",
        "arrival_time": "ALTER TABLE events ADD COLUMN arrival_time TIME NULL",
        "photography_start": "ALTER TABLE events ADD COLUMN photography_start TIME NULL",
        "photography_finish": "ALTER TABLE events ADD COLUMN photography_finish TIME NULL",
    }
    for column_name, statement in statements.items():
        if column_name not in existing:
            connection.execute(text(statement))


@migration("003_event_production_checklist")
def add_event_production_checklist(connection: Connection) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns("events")}
    statements = {
        "booking_confirmed": "ALTER TABLE events ADD COLUMN booking_confirmed BOOLEAN NOT NULL DEFAULT FALSE",
        "contract_received": "ALTER TABLE events ADD COLUMN contract_received BOOLEAN NOT NULL DEFAULT FALSE",
        "photographer_assigned": "ALTER TABLE events ADD COLUMN photographer_assigned BOOLEAN NOT NULL DEFAULT FALSE",
        "equipment_packed": "ALTER TABLE events ADD COLUMN equipment_packed BOOLEAN NOT NULL DEFAULT FALSE",
        "galleries_uploaded": "ALTER TABLE events ADD COLUMN galleries_uploaded BOOLEAN NOT NULL DEFAULT FALSE",
        "gallery_published": "ALTER TABLE events ADD COLUMN gallery_published BOOLEAN NOT NULL DEFAULT FALSE",
        "orders_complete": "ALTER TABLE events ADD COLUMN orders_complete BOOLEAN NOT NULL DEFAULT FALSE",
    }
    for column_name, statement in statements.items():
        if column_name not in existing:
            connection.execute(text(statement))



@migration("004_gallery_foundation")
def create_galleries(connection: Connection) -> None:
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["galleries"]])



@migration("005_transfer_queue_foundation")
def create_transfer_jobs(connection: Connection) -> None:
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["transfer_jobs"]])



@migration("006_sync_api_foundation")
def create_sync_api_foundation(connection: Connection) -> None:
    inspector = inspect(connection)
    event_columns = {column["name"] for column in inspector.get_columns("events")}
    if "source_ref" not in event_columns:
        connection.execute(text("ALTER TABLE events ADD COLUMN source_ref VARCHAR(200) NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_events_source_ref ON events (source_ref)"))
    gallery_columns = {column["name"] for column in inspector.get_columns("galleries")}
    if "source_ref" not in gallery_columns:
        connection.execute(text("ALTER TABLE galleries ADD COLUMN source_ref VARCHAR(200) NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_galleries_source_ref ON galleries (source_ref)"))
    transfer_columns = {column["name"] for column in inspector.get_columns("transfer_jobs")}
    if "source_ref" not in transfer_columns:
        connection.execute(text("ALTER TABLE transfer_jobs ADD COLUMN source_ref VARCHAR(200) NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_transfer_jobs_source_ref ON transfer_jobs (source_ref)"))
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["sync_assets"]])



@migration("007_live_gallery_assets")
def create_live_gallery_assets(connection: Connection) -> None:
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["gallery_assets"]])



@migration("008_customer_delivery")
def create_customer_deliveries(connection: Connection) -> None:
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["customer_deliveries"]])



@migration("009_cloud_checkout_and_mirror")
def create_cloud_checkout_and_mirror(connection: Connection) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns("events")}
    statements = {
        "service_desk_payments_enabled": "ALTER TABLE events ADD COLUMN service_desk_payments_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "event_collection_available": "ALTER TABLE events ADD COLUMN event_collection_available BOOLEAN NOT NULL DEFAULT FALSE",
        "mirrored_customers_browsing": "ALTER TABLE events ADD COLUMN mirrored_customers_browsing INTEGER NOT NULL DEFAULT 0",
        "mirrored_active_favourites": "ALTER TABLE events ADD COLUMN mirrored_active_favourites INTEGER NOT NULL DEFAULT 0",
        "mirrored_active_baskets": "ALTER TABLE events ADD COLUMN mirrored_active_baskets INTEGER NOT NULL DEFAULT 0",
        "mirrored_orders_today": "ALTER TABLE events ADD COLUMN mirrored_orders_today INTEGER NOT NULL DEFAULT 0",
        "mirrored_last_sync_at": "ALTER TABLE events ADD COLUMN mirrored_last_sync_at TIMESTAMP WITH TIME ZONE NULL",
    }
    for column_name, statement in statements.items():
        if column_name not in existing:
            connection.execute(text(statement))
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["cloud_orders"]])



@migration("010_online_print_delivery_charge")
def add_online_print_delivery_charge(connection: Connection) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns("cloud_orders")}
    if "delivery_charge_pence" not in existing:
        connection.execute(text("ALTER TABLE cloud_orders ADD COLUMN delivery_charge_pence INTEGER NOT NULL DEFAULT 0"))



@migration("011_order_sync_and_fulfilment")
def add_order_sync_and_fulfilment(connection: Connection) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns("cloud_orders")}
    statements = {
        "order_reference": "ALTER TABLE cloud_orders ADD COLUMN order_reference VARCHAR(120) NOT NULL DEFAULT ''",
        "payment_status": "ALTER TABLE cloud_orders ADD COLUMN payment_status VARCHAR(30) NOT NULL DEFAULT 'awaiting_payment'",
        "order_items_json": "ALTER TABLE cloud_orders ADD COLUMN order_items_json TEXT NOT NULL DEFAULT '[]'",
        "delivery_id": "ALTER TABLE cloud_orders ADD COLUMN delivery_id VARCHAR(36) NULL",
        "fulfilment_error": "ALTER TABLE cloud_orders ADD COLUMN fulfilment_error TEXT NOT NULL DEFAULT ''",
        "retry_count": "ALTER TABLE cloud_orders ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
        "last_synced_at": "ALTER TABLE cloud_orders ADD COLUMN last_synced_at TIMESTAMP WITH TIME ZONE NULL",
        "completed_at": "ALTER TABLE cloud_orders ADD COLUMN completed_at TIMESTAMP WITH TIME ZONE NULL",
    }
    for column_name, statement in statements.items():
        if column_name not in existing:
            connection.execute(text(statement))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_cloud_orders_payment_status ON cloud_orders (payment_status)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_cloud_orders_delivery_id ON cloud_orders (delivery_id)"))



@migration("012_sumup_payment_completion")
def add_sumup_payment_completion(connection: Connection) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns("cloud_orders")}
    statements = {
        "payment_amount_pence": "ALTER TABLE cloud_orders ADD COLUMN payment_amount_pence INTEGER NOT NULL DEFAULT 0",
        "sumup_checkout_id": "ALTER TABLE cloud_orders ADD COLUMN sumup_checkout_id VARCHAR(80) NULL",
        "sumup_checkout_url": "ALTER TABLE cloud_orders ADD COLUMN sumup_checkout_url TEXT NOT NULL DEFAULT ''",
        "sumup_checkout_reference": "ALTER TABLE cloud_orders ADD COLUMN sumup_checkout_reference VARCHAR(90) NOT NULL DEFAULT ''",
        "sumup_status": "ALTER TABLE cloud_orders ADD COLUMN sumup_status VARCHAR(30) NOT NULL DEFAULT ''",
        "sumup_transaction_code": "ALTER TABLE cloud_orders ADD COLUMN sumup_transaction_code VARCHAR(80) NOT NULL DEFAULT ''",
        "payment_verified_at": "ALTER TABLE cloud_orders ADD COLUMN payment_verified_at TIMESTAMP WITH TIME ZONE NULL",
    }
    for column_name, statement in statements.items():
        if column_name not in existing:
            connection.execute(text(statement))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_cloud_orders_sumup_checkout_id ON cloud_orders (sumup_checkout_id)"))


@migration("013_operations_and_audit")
def add_operations_and_audit(connection: Connection) -> None:
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["order_audit"]])



@migration("014_exact_folder_tree_mirroring")
def add_exact_folder_tree_mirroring(connection: Connection) -> None:
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["gallery_folders"]])
    existing = {column["name"] for column in inspect(connection).get_columns("gallery_assets")}
    if "folder_path" not in existing:
        connection.execute(text("ALTER TABLE gallery_assets ADD COLUMN folder_path VARCHAR(800) NOT NULL DEFAULT ''"))
    if "media_kind" not in existing:
        connection.execute(text("ALTER TABLE gallery_assets ADD COLUMN media_kind VARCHAR(20) NOT NULL DEFAULT 'photos'"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_gallery_assets_folder_path ON gallery_assets (folder_path)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_gallery_assets_media_kind ON gallery_assets (media_kind)"))

@migration("015_private_delivery_assets")
def add_private_delivery_assets(connection: Connection) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns("gallery_assets")}
    if "delivery_storage_path" not in existing:
        connection.execute(text("ALTER TABLE gallery_assets ADD COLUMN delivery_storage_path TEXT NOT NULL DEFAULT ''"))


@migration("016_persistent_customer_favourites")
def create_persistent_customer_favourites(connection: Connection) -> None:
    Base.metadata.create_all(bind=connection, tables=[Base.metadata.tables["customer_favourite_sessions"], Base.metadata.tables["customer_favourites"]])


@migration("017_event_pricing_catalogue")
def add_event_pricing_catalogue(connection: Connection) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns("events")}
    if "pricing_json" not in existing:
        connection.execute(text("ALTER TABLE events ADD COLUMN pricing_json TEXT NOT NULL DEFAULT '[]'"))


@migration("018_on_demand_delivery_and_two_downloads")
def add_on_demand_delivery_and_two_downloads(connection: Connection) -> None:
    asset_columns = {column["name"] for column in inspect(connection).get_columns("gallery_assets")}
    if "highres_delivery_storage_path" not in asset_columns:
        connection.execute(text("ALTER TABLE gallery_assets ADD COLUMN highres_delivery_storage_path TEXT NOT NULL DEFAULT ''"))
    delivery_columns = {column["name"] for column in inspect(connection).get_columns("customer_deliveries")}
    if "download_count" not in delivery_columns:
        connection.execute(text("ALTER TABLE customer_deliveries ADD COLUMN download_count INTEGER NOT NULL DEFAULT 0"))
    if "max_downloads" not in delivery_columns:
        connection.execute(text("ALTER TABLE customer_deliveries ADD COLUMN max_downloads INTEGER NOT NULL DEFAULT 2"))


@migration("019_unlimited_delivery_and_day3_reminder")
def unlimited_delivery_and_day3_reminder(connection: Connection) -> None:
    delivery_columns = {column["name"] for column in inspect(connection).get_columns("customer_deliveries")}
    if "reminder_token_hash" not in delivery_columns:
        connection.execute(text("ALTER TABLE customer_deliveries ADD COLUMN reminder_token_hash VARCHAR(64) NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_customer_deliveries_reminder_token_hash ON customer_deliveries (reminder_token_hash)"))
    # 0 is the explicit marker for unlimited downloads until expires_at.
    connection.execute(text("UPDATE customer_deliveries SET max_downloads=0"))



@migration("020_staff_profile_requests")
def staff_profile_requests(connection: Connection) -> None:
    table = Base.metadata.tables["staff_profile_requests"]
    table.create(bind=connection, checkfirst=True)


@migration("021_event_availability_requests")
def event_availability_requests(connection: Connection) -> None:
    table = Base.metadata.tables["event_availability_requests"]
    table.create(bind=connection, checkfirst=True)



@migration("022_staff_payroll_submissions")
def staff_payroll_submissions(connection: Connection) -> None:
    table = Base.metadata.tables["staff_payroll_submissions"]
    table.create(bind=connection, checkfirst=True)


@migration("023_staff_payroll_login_tokens")
def staff_payroll_login_tokens(connection: Connection) -> None:
    table = Base.metadata.tables["staff_payroll_login_tokens"]
    table.create(bind=connection, checkfirst=True)



@migration("025_staff_orders_login_tokens")
def staff_orders_login_tokens(connection: Connection) -> None:
    table = Base.metadata.tables["staff_orders_login_tokens"]
    table.create(bind=connection, checkfirst=True)


@migration("024_personal_video_foundation")
def personal_video_foundation(connection: Connection) -> None:
    existing = {
        column["name"]
        for column in inspect(connection).get_columns("events")
    }

    if "personal_video_enabled" not in existing:
        connection.execute(
            text(
                "ALTER TABLE events "
                "ADD COLUMN personal_video_enabled "
                "BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )

    if "personal_video_type" not in existing:
        connection.execute(
            text(
                "ALTER TABLE events "
                "ADD COLUMN personal_video_type "
                "VARCHAR(30) NOT NULL DEFAULT ''"
            )
        )

    Base.metadata.tables["personal_video_bookings"].create(
        bind=connection,
        checkfirst=True,
    )
    Base.metadata.tables["personal_video_entries"].create(
        bind=connection,
        checkfirst=True,
    )



@migration("025_stuphie_online_event_master")
def stuphie_online_event_master(connection: Connection) -> None:
    existing = {
        column["name"]
        for column in inspect(connection).get_columns("events")
    }

    statements = {
        "promoter_name":
            "ALTER TABLE events ADD COLUMN promoter_name VARCHAR(200) NOT NULL DEFAULT ''",
        "promoter_email":
            "ALTER TABLE events ADD COLUMN promoter_email VARCHAR(320) NOT NULL DEFAULT ''",
        "promoter_phone":
            "ALTER TABLE events ADD COLUMN promoter_phone VARCHAR(80) NOT NULL DEFAULT ''",
        "staff_required":
            "ALTER TABLE events ADD COLUMN staff_required INTEGER NULL",
        "event_start_time":
            "ALTER TABLE events ADD COLUMN event_start_time TIME NULL",
        "event_end_time":
            "ALTER TABLE events ADD COLUMN event_end_time TIME NULL",
        "travel_time_minutes":
            "ALTER TABLE events ADD COLUMN travel_time_minutes INTEGER NULL",
        "hotel_required":
            "ALTER TABLE events ADD COLUMN hotel_required BOOLEAN NOT NULL DEFAULT FALSE",
        "hotel_info":
            "ALTER TABLE events ADD COLUMN hotel_info TEXT NOT NULL DEFAULT ''",
        "planning_notes":
            "ALTER TABLE events ADD COLUMN planning_notes TEXT NOT NULL DEFAULT ''",
        "photography_enabled":
            "ALTER TABLE events ADD COLUMN photography_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "video_enabled":
            "ALTER TABLE events ADD COLUMN video_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "printing_enabled":
            "ALTER TABLE events ADD COLUMN printing_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "customer_kiosk_enabled":
            "ALTER TABLE events ADD COLUMN customer_kiosk_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "event_ready":
            "ALTER TABLE events ADD COLUMN event_ready BOOLEAN NOT NULL DEFAULT FALSE",
        "event_ready_at":
            "ALTER TABLE events ADD COLUMN event_ready_at TIMESTAMP WITH TIME ZONE NULL",
        "package_version":
            "ALTER TABLE events ADD COLUMN package_version INTEGER NOT NULL DEFAULT 0",
        "package_state":
            "ALTER TABLE events ADD COLUMN package_state VARCHAR(30) NOT NULL DEFAULT 'not_published'",
        "package_published_at":
            "ALTER TABLE events ADD COLUMN package_published_at TIMESTAMP WITH TIME ZONE NULL",
        "package_published_by":
            "ALTER TABLE events ADD COLUMN package_published_by VARCHAR(320) NOT NULL DEFAULT ''",
    }

    for column_name, statement in statements.items():
        if column_name not in existing:
            connection.execute(text(statement))



def run_migrations(engine: Engine) -> None:
    metadata = MetaData()
    migrations = Table(
        "schema_migrations",
        metadata,
        Column("version", String(100), primary_key=True),
        Column("applied_at", DateTime(timezone=True), server_default=None),
    )
    metadata.create_all(engine, tables=[migrations])

    with engine.begin() as connection:
        applied = set(connection.execute(select(migrations.c.version)).scalars())
        for version, function in MIGRATIONS:
            if version in applied:
                continue
            LOGGER.info("Applying database migration %s", version)
            function(connection)
            connection.execute(migrations.insert().values(version=version))
            LOGGER.info("Applied database migration %s", version)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    required_tables = {
        "events",
        "galleries",
        "transfer_jobs",
        "sync_assets",
        "gallery_assets",
        "gallery_folders",
        "customer_deliveries",
        "cloud_orders",
        "order_audit",
        "customer_favourite_sessions",
        "customer_favourites",
        "staff_profile_requests",
        "event_availability_requests",
        "staff_payroll_submissions",
        "staff_payroll_login_tokens",
        "personal_video_bookings",
        "personal_video_entries",
    }
    if not required_tables.issubset(tables):
        missing = sorted(required_tables - tables)
        raise RuntimeError(
            "Database migration completed without creating required tables: "
            + ", ".join(missing)
        )


@migration("026_staff_master_and_event_assignments")
def staff_master_and_event_assignments(connection: Connection) -> None:
    staff_table = Base.metadata.tables["staff_members"]

    assignment_table = Base.metadata.tables["event_staff_assignments"]

    staff_table.create(bind=connection, checkfirst=True)
    assignment_table.create(bind=connection, checkfirst=True)


@migration("029_event_staff_shifts")
def event_staff_shifts(connection: Connection) -> None:
    """
    Operational event attendance foundation.

    Payroll remains independent.
    """
    table = Base.metadata.tables["event_staff_shifts"]
    table.create(bind=connection, checkfirst=True)


@migration("030_staff_payroll_management_approval")
def staff_payroll_management_approval(connection: Connection) -> None:
    existing = {
        column["name"]
        for column in inspect(connection).get_columns(
            "staff_payroll_submissions"
        )
    }

    if "approved_at" not in existing:
        connection.execute(
            text(
                "ALTER TABLE staff_payroll_submissions "
                "ADD COLUMN approved_at "
                "TIMESTAMP WITH TIME ZONE NULL"
            )
        )

    if "approved_by" not in existing:
        connection.execute(
            text(
                "ALTER TABLE staff_payroll_submissions "
                "ADD COLUMN approved_by "
                "VARCHAR(320) NOT NULL DEFAULT ''"
            )
        )


@migration("031_staff_payroll_payment_audit")
def staff_payroll_payment_audit(connection: Connection) -> None:
    existing = {
        column["name"]
        for column in inspect(connection).get_columns(
            "staff_payroll_submissions"
        )
    }

    if "paid_at" not in existing:
        connection.execute(
            text(
                "ALTER TABLE staff_payroll_submissions "
                "ADD COLUMN paid_at "
                "TIMESTAMP WITH TIME ZONE NULL"
            )
        )

    if "paid_by" not in existing:
        connection.execute(
            text(
                "ALTER TABLE staff_payroll_submissions "
                "ADD COLUMN paid_by "
                "VARCHAR(320) NOT NULL DEFAULT ''"
            )
        )

    if "payment_reference" not in existing:
        connection.execute(
            text(
                "ALTER TABLE staff_payroll_submissions "
                "ADD COLUMN payment_reference "
                "VARCHAR(200) NOT NULL DEFAULT ''"
            )
        )


@migration("032_staff_authentication_foundation")
def staff_authentication_foundation(connection: Connection) -> None:
    """
    Individual Stuphie Online staff authentication foundation.

    Existing staff remain unable to use individual password login until
    login_enabled is explicitly activated.
    """
    existing = {
        column["name"]
        for column in inspect(connection).get_columns("staff_members")
    }

    statements = {
        "password_hash": (
            "ALTER TABLE staff_members "
            "ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''"
        ),
        "password_changed_at": (
            "ALTER TABLE staff_members "
            "ADD COLUMN password_changed_at TIMESTAMP WITH TIME ZONE NULL"
        ),
        "login_enabled": (
            "ALTER TABLE staff_members "
            "ADD COLUMN login_enabled BOOLEAN NOT NULL DEFAULT FALSE"
        ),
    }

    for column_name, statement in statements.items():
        if column_name not in existing:
            connection.execute(text(statement))

@migration("033_staff_password_reset_tokens")
def staff_password_reset_tokens(connection: Connection) -> None:
    """
    Secure one-time password-reset token storage.

    Raw reset tokens are never persisted.
    """
    table = Base.metadata.tables["staff_password_resets"]
    table.create(bind=connection, checkfirst=True)


@migration("034_staff_hub_private_profile")
def staff_hub_private_profile(connection: Connection) -> None:
    """
    Permanent Staff Hub profile foundation.

    Directory-safe fields are stored separately from the confidential
    employee profile payload. Existing staff records are preserved.
    """
    existing = {
        column["name"]
        for column in inspect(connection).get_columns("staff_members")
    }

    statements = {
        "telephone": (
            "ALTER TABLE staff_members "
            "ADD COLUMN telephone VARCHAR(80) NOT NULL DEFAULT ''"
        ),
        "private_profile_json": (
            "ALTER TABLE staff_members "
            "ADD COLUMN private_profile_json TEXT NOT NULL DEFAULT ''"
        ),
        "photo_storage_path": (
            "ALTER TABLE staff_members "
            "ADD COLUMN photo_storage_path TEXT NOT NULL DEFAULT ''"
        ),
        "photo_filename": (
            "ALTER TABLE staff_members "
            "ADD COLUMN photo_filename VARCHAR(240) NOT NULL DEFAULT ''"
        ),
        "photo_content_type": (
            "ALTER TABLE staff_members "
            "ADD COLUMN photo_content_type VARCHAR(120) NOT NULL DEFAULT ''"
        ),
        "profile_completed_at": (
            "ALTER TABLE staff_members "
            "ADD COLUMN profile_completed_at "
            "TIMESTAMP WITH TIME ZONE NULL"
        ),
    }

    for column_name, statement in statements.items():
        if column_name not in existing:
            connection.execute(text(statement))


@migration("035_staff_documents")
def staff_documents(connection: Connection) -> None:
    """
    Private Policies & Documents library.
    """
    table = Base.metadata.tables["staff_documents"]
    table.create(
        bind=connection,
        checkfirst=True,
    )


@migration("036_staff_document_views")
def staff_document_views(connection: Connection) -> None:
    """
    Per-view Staff Hub document audit history.
    """
    table = Base.metadata.tables["staff_document_views"]
    table.create(
        bind=connection,
        checkfirst=True,
    )



@migration("037_staff_event_interests")
def staff_event_interests(connection: Connection) -> None:
    """
    Staff expressions of interest in Event Master events.
    Existing events, assignments and availability remain unchanged.
    """

    table = Base.metadata.tables["staff_event_interests"]

    table.create(
        bind=connection,
        checkfirst=True,
    )


@migration("038_staff_document_acknowledgements")
def staff_document_acknowledgements(
    connection: Connection,
) -> None:
    """
    Add acknowledgement support to the existing private
    Staff Hub document system.
    """

    inspector = inspect(connection)

    columns = {
        column["name"]
        for column in inspector.get_columns(
            "staff_documents"
        )
    }

    if "requires_acknowledgement" not in columns:
        connection.execute(
            text(
                "ALTER TABLE staff_documents "
                "ADD COLUMN requires_acknowledgement "
                "BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )

    table = Base.metadata.tables[
        "staff_document_acknowledgements"
    ]

    table.create(
        bind=connection,
        checkfirst=True,
    )


@migration("039_staff_additional_work")
def staff_additional_work(connection: Connection) -> None:
    """
    Staff Additional Work submission and approval foundation.

    Additive only. Existing event shifts and Payroll records are
    preserved unchanged.
    """
    table = Base.metadata.tables["staff_additional_work"]
    table.create(
        bind=connection,
        checkfirst=True,
    )


@migration("040_staff_private_payment_setup")
def staff_private_payment_setup(
    connection: Connection,
) -> None:
    """
    Private Payroll Admin payment arrangements and
    effective-dated staff rate history.

    Additive only. Existing payroll submissions, event shifts,
    Additional Work and staff profiles remain unchanged.
    """

    Base.metadata.tables[
        "staff_payment_profiles"
    ].create(
        bind=connection,
        checkfirst=True,
    )

    Base.metadata.tables[
        "staff_pay_rates"
    ].create(
        bind=connection,
        checkfirst=True,
    )


@migration("041_customer_verified_identity")
def customer_verified_identity(connection: Connection) -> None:
    """Create the verified customer identity and OTP challenge tables."""

    Base.metadata.create_all(
        bind=connection,
        tables=[
            Base.metadata.tables["customer_identities"],
            Base.metadata.tables["customer_verification_challenges"],
        ],
    )


@migration("042_customer_consent")
def customer_consent(connection: Connection) -> None:
    """Create versioned verified-customer consent history."""

    Base.metadata.tables["customer_consents"].create(
        bind=connection,
        checkfirst=True,
    )



@migration("043_customer_gallery_activity")
def customer_gallery_activity(connection: Connection) -> None:
    """Verified online customer presence and engagement."""

    Base.metadata.tables["customer_gallery_activity"].create(
        bind=connection,
        checkfirst=True,
    )


@migration("044_customer_reward_snapshot")
def customer_reward_snapshot(connection: Connection) -> None:
    """Create the read-only Sophie’s Rewards balance snapshot."""

    Base.metadata.tables["customer_reward_snapshots"].create(
        bind=connection,
        checkfirst=True,
    )

@migration("045_event_logo_storage_path")
def event_logo_storage_path(connection: Connection) -> None:
    """Add the Stuphie Event Master logo reference to Customer Cloud events."""
    existing = {
        column["name"]
        for column in inspect(connection).get_columns("events")
    }

    if "event_logo_storage_path" not in existing:
        connection.execute(
            text(
                "ALTER TABLE events "
                "ADD COLUMN event_logo_storage_path "
                "TEXT NOT NULL DEFAULT ''"
            )
        )

@migration("046_event_master_source_ref")
def event_master_source_ref(connection: Connection) -> None:
    """Add permanent STUPHIE Event Master identity to Customer Cloud events."""
    existing = {
        column["name"]
        for column in inspect(connection).get_columns("events")
    }

    if "master_source_ref" not in existing:
        connection.execute(
            text(
                "ALTER TABLE events "
                "ADD COLUMN master_source_ref VARCHAR(200) NULL"
            )
        )

    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ix_events_master_source_ref "
            "ON events (master_source_ref)"
        )
    )


@migration("047_event_gallery_access_code")
def event_gallery_access_code(connection: Connection) -> None:
    """Store the Stuphie-owned customer gallery access-code hash on events."""
    existing = {
        column["name"]
        for column in inspect(connection).get_columns("events")
    }

    if "gallery_access_code_hash" not in existing:
        connection.execute(
            text(
                "ALTER TABLE events "
                "ADD COLUMN gallery_access_code_hash "
                "TEXT NOT NULL DEFAULT ''"
            )
        )

@migration("048_event_brand_id")
def event_brand_id(connection: Connection) -> None:
    """Store the white-label brand identity for each cloud event."""

    existing = {
        column["name"]
        for column in inspect(connection).get_columns("events")
    }

    if "brand_id" not in existing:
        connection.execute(
            text(
                "ALTER TABLE events "
                "ADD COLUMN brand_id "
                "VARCHAR(80) NOT NULL DEFAULT 'sophies'"
            )
        )

    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS "
            "ix_events_brand_id "
            "ON events (brand_id)"
        )
    )
