from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.customer_identity import remember_verified_customer
from app.customer_ownership import (
    favourite_session_for_verified_customer,
    verified_customer_profile,
)
from app.db.models import (
    Base,
    CustomerFavouriteSession,
    CustomerIdentity,
    Event,
    Gallery,
)


class RequestStub:
    def __init__(self):
        self.session = {}


def make_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(engine)

    Session = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    return engine, Session()


def make_identity(db):
    identity = CustomerIdentity(
        email="customer@example.com",
        first_name="Sophie",
        last_name="Customer",
        verified_at=datetime.now(timezone.utc),
    )

    db.add(identity)
    db.commit()
    db.refresh(identity)

    return identity


def make_gallery(db):
    event = Event(
        name="V2 Test Event",
        start_date=datetime.now(timezone.utc).date(),
        end_date=datetime.now(timezone.utc).date(),
        status="live",
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    gallery = Gallery(
        event_id=event.id,
        name="V2 Test Gallery",
        slug="v2-test",
        status="published",
    )

    db.add(gallery)
    db.commit()
    db.refresh(gallery)

    return gallery


def test_verified_profile_comes_from_signed_identity():
    engine, db = make_db()

    try:
        identity = make_identity(db)

        request = RequestStub()

        remember_verified_customer(
            request.session,
            identity,
        )

        profile = verified_customer_profile(
            db,
            request,
        )

        assert profile is not None
        assert profile["identity_id"] == str(identity.id)
        assert profile["email"] == "customer@example.com"
        assert profile["name"] == "Sophie Customer"
    finally:
        db.close()
        engine.dispose()


def test_unverified_browser_has_no_customer_profile():
    engine, db = make_db()

    try:
        request = RequestStub()

        assert (
            verified_customer_profile(
                db,
                request,
            )
            is None
        )
    finally:
        db.close()
        engine.dispose()


def test_verified_customer_creates_own_favourite_session():
    engine, db = make_db()

    try:
        identity = make_identity(db)
        gallery = make_gallery(db)

        request = RequestStub()

        remember_verified_customer(
            request.session,
            identity,
        )

        favourite_session = (
            favourite_session_for_verified_customer(
                db,
                request,
                gallery,
                create=True,
            )
        )

        assert favourite_session is not None
        assert favourite_session.email == identity.email
        assert (
            favourite_session.customer_name
            == "Sophie Customer"
        )
        assert favourite_session.token

        stored = db.get(
            CustomerFavouriteSession,
            favourite_session.id,
        )

        assert stored is not None
        assert stored.email == "customer@example.com"
    finally:
        db.close()
        engine.dispose()


def test_verified_customer_reconnects_existing_favourites():
    engine, db = make_db()

    try:
        identity = make_identity(db)
        gallery = make_gallery(db)

        existing = CustomerFavouriteSession(
            gallery_id=gallery.id,
            token="existing-test-token",
            customer_name="Old Name",
            email="customer@example.com",
            retention_choice="temporary_link",
        )

        db.add(existing)
        db.commit()
        db.refresh(existing)

        request = RequestStub()

        remember_verified_customer(
            request.session,
            identity,
        )

        resolved = (
            favourite_session_for_verified_customer(
                db,
                request,
                gallery,
                create=False,
            )
        )

        assert resolved is not None
        assert resolved.id == existing.id
        assert resolved.token == "existing-test-token"
        assert resolved.email == identity.email
    finally:
        db.close()
        engine.dispose()


def test_submitted_email_cannot_change_verified_owner():
    engine, db = make_db()

    try:
        identity = make_identity(db)
        gallery = make_gallery(db)

        request = RequestStub()

        remember_verified_customer(
            request.session,
            identity,
        )

        attacker_supplied_email = "someoneelse@example.com"

        favourite_session = (
            favourite_session_for_verified_customer(
                db,
                request,
                gallery,
                create=True,
            )
        )

        assert attacker_supplied_email != identity.email
        assert (
            favourite_session.email
            == "customer@example.com"
        )
        assert (
            favourite_session.email
            != attacker_supplied_email
        )
    finally:
        db.close()
        engine.dispose()
