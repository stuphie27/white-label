from sqlalchemy import inspect

from app.db.models import (
    PersonalVideoBooking,
    PersonalVideoEntry,
)
from app.db.session import (
    build_engine,
    database_schema_is_ready,
)
from app.migrations.runner import run_migrations


def test_personal_video_models_use_separate_tables():
    assert PersonalVideoBooking.__tablename__ == (
        "personal_video_bookings"
    )
    assert PersonalVideoEntry.__tablename__ == (
        "personal_video_entries"
    )


def test_personal_video_cloud_foundation_migrates():
    engine = build_engine("sqlite+pysqlite:///:memory:")

    run_migrations(engine)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert "personal_video_bookings" in tables
    assert "personal_video_entries" in tables

    event_columns = {
        column["name"]
        for column in inspector.get_columns("events")
    }

    assert "personal_video_enabled" in event_columns
    assert "personal_video_type" in event_columns

    booking_columns = {
        column["name"]
        for column in inspector.get_columns(
            "personal_video_bookings"
        )
    }

    assert {
        "source_ref",
        "event_id",
        "reference",
        "manage_token",
        "booking_type",
        "customer_name",
        "customer_email",
        "payment_status",
        "initial_payment_pence",
        "total_paid_pence",
        "final_total_pence",
        "minimum_video_count",
        "video_format",
        "intended_use",
        "terms_accepted_at",
        "terms_version",
    }.issubset(booking_columns)

    entry_columns = {
        column["name"]
        for column in inspector.get_columns(
            "personal_video_entries"
        )
    }

    assert {
        "source_ref",
        "booking_id",
        "dancer_name",
        "dancer_number",
        "dance_style",
        "category",
        "age_group",
        "event_number",
        "event_name",
        "dance_name",
        "performance_date",
        "performance_time",
        "expected_video_count",
        "filming_instruction",
        "filming_details",
        "unit_price_pence",
        "external_video_link",
    }.issubset(entry_columns)

    assert database_schema_is_ready(engine) is True


def test_personal_video_migration_is_idempotent():
    engine = build_engine("sqlite+pysqlite:///:memory:")

    run_migrations(engine)
    run_migrations(engine)

    inspector = inspect(engine)

    assert "personal_video_bookings" in (
        inspector.get_table_names()
    )
    assert "personal_video_entries" in (
        inspector.get_table_names()
    )
    assert database_schema_is_ready(engine) is True
