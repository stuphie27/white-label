from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.customer_consent import (
    CUSTOMER_COPYRIGHT_VERSION,
    CUSTOMER_PRIVACY_VERSION,
    CUSTOMER_TERMS_VERSION,
    has_current_customer_consent,
    record_customer_consent,
)
from app.db.models import (
    Base,
    CustomerConsent,
    CustomerIdentity,
)


def make_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )
    Base.metadata.create_all(engine)

    Session = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    return engine, Session()


def test_records_versioned_customer_consent():
    engine, db = make_db()

    try:
        identity = CustomerIdentity(
            email="customer@example.com",
            first_name="Sophie",
            last_name="Customer",
            verified_at=datetime.now(timezone.utc),
        )
        db.add(identity)
        db.commit()
        db.refresh(identity)

        consent = record_customer_consent(
            db,
            identity.id,
        )

        assert consent.customer_identity_id == identity.id
        assert consent.terms_version == CUSTOMER_TERMS_VERSION
        assert consent.privacy_version == CUSTOMER_PRIVACY_VERSION
        assert (
            consent.copyright_version
            == CUSTOMER_COPYRIGHT_VERSION
        )
        assert consent.accepted_at is not None
        assert has_current_customer_consent(
            db,
            identity.id,
        )
    finally:
        db.close()
        engine.dispose()


def test_same_current_consent_is_not_duplicated():
    engine, db = make_db()

    try:
        identity = CustomerIdentity(
            email="repeat@example.com",
            first_name="Repeat",
            last_name="Customer",
            verified_at=datetime.now(timezone.utc),
        )
        db.add(identity)
        db.commit()
        db.refresh(identity)

        first = record_customer_consent(
            db,
            identity.id,
        )
        second = record_customer_consent(
            db,
            identity.id,
        )

        assert first.id == second.id

        records = list(
            db.scalars(
                select(CustomerConsent).where(
                    CustomerConsent.customer_identity_id
                    == identity.id
                )
            )
        )

        assert len(records) == 1
    finally:
        db.close()
        engine.dispose()


def test_migration_042_exists():
    from pathlib import Path

    runner = (
        Path(__file__).resolve().parents[1]
        / "app/migrations/runner.py"
    ).read_text()

    assert '@migration("042_customer_consent")' in runner
    assert (
        'Base.metadata.tables["customer_consents"]'
        in runner
    )
