from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.customer_identity import (
    CustomerVerificationError,
    create_customer_verification,
    customer_code_digest,
    generate_customer_code,
    normalise_customer_email,
    remember_verified_customer,
    verified_customer_id,
    verify_customer_code,
)
from app.db.models import (
    Base,
    CustomerIdentity,
    CustomerVerificationChallenge,
)


SECRET = (
    "r7-test-secret-that-is-long-enough-"
    "and-never-used-in-production"
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )
    Base.metadata.create_all(engine)

    Session = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    with Session() as session:
        yield session

    engine.dispose()


def test_email_is_canonicalised():
    assert (
        normalise_customer_email(
            "  Test.Customer@Example.COM "
        )
        == "test.customer@example.com"
    )


def test_generated_code_is_exactly_six_digits():
    for _ in range(50):
        code = generate_customer_code()
        assert len(code) == 6
        assert code.isdigit()


def test_raw_code_is_never_stored(db):
    challenge, code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="customer@example.com",
        first_name="Sophie",
        last_name="Customer",
    )

    assert challenge.code_digest != code
    assert len(challenge.code_digest) == 64

    stored = db.scalar(
        select(CustomerVerificationChallenge)
    )

    assert stored.code_digest == (
        customer_code_digest(
            SECRET,
            "customer@example.com",
            code,
        )
    )


def test_correct_code_creates_verified_identity(db):
    challenge, code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="Customer@Example.com",
        first_name="Sophie",
        last_name="Customer",
    )

    identity = verify_customer_code(
        db,
        secret_key=SECRET,
        email="customer@example.com",
        code=code,
    )

    assert identity.email == "customer@example.com"
    assert identity.first_name == "Sophie"
    assert identity.last_name == "Customer"
    assert identity.verified_at is not None

    db.refresh(challenge)
    assert challenge.used_at is not None


def test_same_email_reuses_existing_identity(db):
    _, first_code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="same@example.com",
        first_name="First",
        last_name="Customer",
    )

    first = verify_customer_code(
        db,
        secret_key=SECRET,
        email="same@example.com",
        code=first_code,
    )

    later = datetime.now(timezone.utc) + timedelta(
        minutes=2
    )

    _, second_code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="SAME@example.com",
        first_name="Updated",
        last_name="Customer",
        now=later,
    )

    second = verify_customer_code(
        db,
        secret_key=SECRET,
        email="same@example.com",
        code=second_code,
        now=later,
    )

    assert second.id == first.id
    assert second.first_name == "Updated"

    identities = list(
        db.scalars(select(CustomerIdentity))
    )

    assert len(identities) == 1


def test_wrong_code_increments_attempts(db):
    challenge, code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="wrong@example.com",
    )

    wrong = "000000"
    if wrong == code:
        wrong = "999999"

    with pytest.raises(
        CustomerVerificationError
    ) as exc:
        verify_customer_code(
            db,
            secret_key=SECRET,
            email="wrong@example.com",
            code=wrong,
        )

    assert exc.value.code == "invalid_code"

    db.refresh(challenge)
    assert challenge.attempts == 1


def test_five_wrong_attempts_lock_challenge(db):
    challenge, code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="locked@example.com",
    )

    wrong = "000000"
    if wrong == code:
        wrong = "999999"

    for _ in range(5):
        with pytest.raises(
            CustomerVerificationError
        ):
            verify_customer_code(
                db,
                secret_key=SECRET,
                email="locked@example.com",
                code=wrong,
            )

    db.refresh(challenge)

    assert challenge.attempts == 5
    assert challenge.used_at is not None


def test_expired_code_is_rejected(db):
    start = datetime(
        2026,
        8,
        31,
        20,
        0,
        tzinfo=timezone.utc,
    )

    challenge, code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="expired@example.com",
        now=start,
        ttl_seconds=600,
    )

    with pytest.raises(
        CustomerVerificationError
    ) as exc:
        verify_customer_code(
            db,
            secret_key=SECRET,
            email="expired@example.com",
            code=code,
            now=start + timedelta(minutes=11),
        )

    assert exc.value.code == "expired"

    db.refresh(challenge)
    assert challenge.used_at is not None


def test_code_is_single_use(db):
    _, code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="single@example.com",
    )

    verify_customer_code(
        db,
        secret_key=SECRET,
        email="single@example.com",
        code=code,
    )

    with pytest.raises(
        CustomerVerificationError
    ) as exc:
        verify_customer_code(
            db,
            secret_key=SECRET,
            email="single@example.com",
            code=code,
        )

    assert exc.value.code == "used"


def test_resend_is_rate_limited(db):
    start = datetime(
        2026,
        8,
        31,
        20,
        0,
        tzinfo=timezone.utc,
    )

    create_customer_verification(
        db,
        secret_key=SECRET,
        email="resend@example.com",
        now=start,
    )

    with pytest.raises(
        CustomerVerificationError
    ) as exc:
        create_customer_verification(
            db,
            secret_key=SECRET,
            email="resend@example.com",
            now=start + timedelta(seconds=30),
        )

    assert exc.value.code == "resend_too_soon"


def test_verified_identity_is_remembered_by_stable_id(db):
    _, code = create_customer_verification(
        db,
        secret_key=SECRET,
        email="session@example.com",
    )

    identity = verify_customer_code(
        db,
        secret_key=SECRET,
        email="session@example.com",
        code=code,
    )

    session_state = {}

    remember_verified_customer(
        session_state,
        identity,
    )

    assert (
        verified_customer_id(session_state)
        == identity.id
    )
    assert "verified_customer_at" in session_state
    assert "session@example.com" not in str(
        session_state
    )


def test_migration_041_creates_only_identity_tables_contract():
    runner = (
        Path(__file__).resolve().parents[1]
        / "app/migrations/runner.py"
    ).read_text()

    assert (
        '@migration("041_customer_verified_identity")'
        in runner
    )
    assert 'Base.metadata.tables["customer_identities"]' in runner
    assert (
        'Base.metadata.tables["customer_verification_challenges"]'
        in runner
    )


def test_identity_service_has_no_email_sender():
    source = (
        Path(__file__).resolve().parents[1]
        / "app/customer_identity.py"
    ).read_text()

    assert "smtplib" not in source
    assert "send_message" not in source
