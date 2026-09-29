from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import CustomerConsent


CUSTOMER_TERMS_VERSION = "2026-09-01"
CUSTOMER_PRIVACY_VERSION = "2026-09-01"
CUSTOMER_COPYRIGHT_VERSION = "2026-09-01"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current_customer_consent(db, customer_identity_id: str):
    return db.scalar(
        select(CustomerConsent)
        .where(
            CustomerConsent.customer_identity_id
            == str(customer_identity_id)
        )
        .where(
            CustomerConsent.terms_version
            == CUSTOMER_TERMS_VERSION
        )
        .where(
            CustomerConsent.privacy_version
            == CUSTOMER_PRIVACY_VERSION
        )
        .where(
            CustomerConsent.copyright_version
            == CUSTOMER_COPYRIGHT_VERSION
        )
        .order_by(CustomerConsent.accepted_at.desc())
        .limit(1)
    )


def has_current_customer_consent(
    db,
    customer_identity_id: str,
) -> bool:
    return (
        current_customer_consent(
            db,
            customer_identity_id,
        )
        is not None
    )


def record_customer_consent(
    db,
    customer_identity_id: str,
    *,
    now: datetime | None = None,
) -> CustomerConsent:
    existing = current_customer_consent(
        db,
        customer_identity_id,
    )

    if existing is not None:
        return existing

    consent = CustomerConsent(
        customer_identity_id=str(customer_identity_id),
        terms_version=CUSTOMER_TERMS_VERSION,
        privacy_version=CUSTOMER_PRIVACY_VERSION,
        copyright_version=CUSTOMER_COPYRIGHT_VERSION,
        accepted_at=now or _now(),
    )

    db.add(consent)
    db.commit()
    db.refresh(consent)

    return consent
