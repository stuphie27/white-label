from __future__ import annotations

import secrets

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.customer_identity import verified_customer_id
from app.db.models import (
    CustomerFavouriteSession,
    CustomerIdentity,
    Event,
)


def verified_customer(db, request):
    identity_id = verified_customer_id(request.session)

    if not identity_id:
        return None

    identity = db.get(CustomerIdentity, identity_id)

    if identity is None:
        request.session.pop("verified_customer_id", None)
        request.session.pop("verified_customer_at", None)
        return None

    return identity


def verified_customer_profile(db, request) -> dict | None:
    identity = verified_customer(db, request)

    if identity is None:
        return None

    name = " ".join(
        part
        for part in [
            str(identity.first_name or "").strip(),
            str(identity.last_name or "").strip(),
        ]
        if part
    )

    return {
        "identity_id": str(identity.id),
        "name": name,
        "email": str(identity.email or "").strip().lower(),
    }


def favourite_session_for_verified_customer(
    db,
    request,
    gallery,
    *,
    create: bool = False,
):
    profile = verified_customer_profile(db, request)

    if profile is None:
        return None

    email = profile["email"]

    favourite_session = db.scalar(
        select(CustomerFavouriteSession).where(
            CustomerFavouriteSession.gallery_id == gallery.id,
            CustomerFavouriteSession.email == email,
        )
    )

    if favourite_session is not None:
        if profile["name"]:
            favourite_session.customer_name = profile["name"]

        return favourite_session

    if not create:
        return None

    event = db.get(Event, gallery.event_id)

    free_expiry = (
        None
        if event and event.status == "live"
        else datetime.now(timezone.utc) + timedelta(days=5)
    )

    favourite_session = CustomerFavouriteSession(
        gallery_id=gallery.id,
        token=secrets.token_urlsafe(32),
        customer_name=profile["name"],
        email=email,
        retention_choice="temporary_link",
        expires_at=free_expiry,
    )

    db.add(favourite_session)
    db.commit()
    db.refresh(favourite_session)

    return favourite_session
