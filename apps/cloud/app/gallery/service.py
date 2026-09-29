from __future__ import annotations

import re
import secrets
from datetime import date

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Event, Gallery

GALLERY_STATUSES = ("draft", "uploading", "ready", "published", "archived")
GALLERY_VISIBILITIES = ("private", "public")
PASSWORD_HASH = PasswordHasher()


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return value or "gallery"


def unique_slug(session: Session, value: str, *, exclude_id: str | None = None) -> str:
    base = slugify(value)
    candidate = base
    suffix = 2
    while True:
        statement = select(Gallery.id).where(Gallery.slug == candidate)
        if exclude_id:
            statement = statement.where(Gallery.id != exclude_id)
        if session.scalar(statement) is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


def list_galleries(session: Session, *, status: str = "") -> list[tuple[Gallery, Event]]:
    statement = select(Gallery, Event).join(Event, Event.id == Gallery.event_id)
    if status in GALLERY_STATUSES:
        statement = statement.where(Gallery.status == status)
    statement = statement.order_by(Gallery.updated_at.desc())
    return list(session.execute(statement).all())


def count_galleries(session: Session, *, status: str | None = None) -> int:
    statement = select(func.count()).select_from(Gallery)
    if status:
        statement = statement.where(Gallery.status == status)
    return int(session.scalar(statement) or 0)


def gallery_status_counts(session: Session) -> dict[str, int]:
    counts = {status: 0 for status in GALLERY_STATUSES}
    for status, total in session.execute(select(Gallery.status, func.count()).group_by(Gallery.status)).all():
        counts[str(status)] = int(total)
    return counts


def get_gallery(session: Session, gallery_id: str) -> Gallery | None:
    return session.get(Gallery, gallery_id)


def get_gallery_by_slug(session: Session, slug: str) -> Gallery | None:
    return session.scalar(select(Gallery).where(Gallery.slug == slug))


def create_gallery(
    session: Session,
    *,
    event_id: str,
    name: str,
    slug: str,
    status: str,
    visibility: str,
    access_code: str,
    expires_at: date | None,
    description: str,
) -> Gallery:
    gallery = Gallery(
        event_id=event_id,
        name=name,
        slug=unique_slug(session, slug or name),
        status=status,
        visibility=visibility,
        access_code_hash=PASSWORD_HASH.hash(access_code) if access_code else "",
        expires_at=expires_at,
        description=description,
    )
    session.add(gallery)
    session.commit()
    session.refresh(gallery)
    return gallery


def update_gallery(session: Session, gallery: Gallery, **values: object) -> Gallery:
    access_code = values.pop("access_code", None)
    if access_code:
        gallery.access_code_hash = PASSWORD_HASH.hash(str(access_code))
    for key, value in values.items():
        setattr(gallery, key, value)
    session.commit()
    session.refresh(gallery)
    return gallery


def verify_access_code(gallery: Gallery, supplied: str) -> bool:
    if not gallery.access_code_hash:
        return True

    supplied = str(supplied or "").strip()

    try:
        if PASSWORD_HASH.verify(
            gallery.access_code_hash,
            supplied,
        ):
            return True
    except (VerifyMismatchError, VerificationError):
        pass

    normalised = supplied.upper()

    if normalised == supplied:
        return False

    try:
        return PASSWORD_HASH.verify(
            gallery.access_code_hash,
            normalised,
        )
    except (VerifyMismatchError, VerificationError):
        return False


def random_access_code() -> str:
    return str(secrets.randbelow(900000) + 100000)
