from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import (
    CustomerIdentity,
    CustomerVerificationChallenge,
)


CUSTOMER_OTP_TTL_SECONDS = 600
CUSTOMER_OTP_MAX_ATTEMPTS = 5
CUSTOMER_OTP_RESEND_SECONDS = 60

_EMAIL_RE = re.compile(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
)


class CustomerVerificationError(ValueError):
    """Expected customer-verification failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalise_customer_email(value: str) -> str:
    email = str(value or "").strip().lower()[:320]

    if not email or not _EMAIL_RE.match(email):
        raise CustomerVerificationError(
            "invalid_email",
            "Please enter a valid email address.",
        )

    return email


def clean_customer_name(value: str) -> str:
    return " ".join(
        str(value or "").strip().split()
    )[:120]


def generate_customer_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def customer_code_digest(
    secret_key: str,
    email: str,
    code: str,
) -> str:
    canonical_email = normalise_customer_email(email)

    payload = (
        f"stuphie-customer-otp:{canonical_email}:{code}"
    ).encode("utf-8")

    return hmac.new(
        str(secret_key).encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def _latest_challenge(db, email: str):
    return db.scalar(
        select(CustomerVerificationChallenge)
        .where(
            CustomerVerificationChallenge.email == email
        )
        .order_by(
            CustomerVerificationChallenge.created_at.desc()
        )
        .limit(1)
    )


def create_customer_verification(
    db,
    *,
    secret_key: str,
    email: str,
    first_name: str = "",
    last_name: str = "",
    now: datetime | None = None,
    ttl_seconds: int = CUSTOMER_OTP_TTL_SECONDS,
    resend_seconds: int = CUSTOMER_OTP_RESEND_SECONDS,
) -> tuple[CustomerVerificationChallenge, str]:
    """Create a single-use six-digit verification challenge.

    Returns the raw code exactly once so the calling email layer can send it.
    Only its keyed SHA-256 digest is persisted.
    """

    current = _as_utc(now or _now())
    canonical_email = normalise_customer_email(email)

    first = clean_customer_name(first_name)
    last = clean_customer_name(last_name)

    latest = _latest_challenge(db, canonical_email)

    if latest is not None and latest.used_at is None:
        created_at = _as_utc(latest.created_at)

        if (
            current - created_at
        ) < timedelta(seconds=resend_seconds):
            raise CustomerVerificationError(
                "resend_too_soon",
                "Please wait before requesting another code.",
            )

        latest.used_at = current

    code = generate_customer_code()

    challenge = CustomerVerificationChallenge(
        email=canonical_email,
        first_name=first,
        last_name=last,
        code_digest=customer_code_digest(
            secret_key,
            canonical_email,
            code,
        ),
        expires_at=current + timedelta(
            seconds=ttl_seconds
        ),
        attempts=0,
        max_attempts=CUSTOMER_OTP_MAX_ATTEMPTS,
        created_at=current,
    )

    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    return challenge, code


def verify_customer_code(
    db,
    *,
    secret_key: str,
    email: str,
    code: str,
    now: datetime | None = None,
) -> CustomerIdentity:
    current = _as_utc(now or _now())
    canonical_email = normalise_customer_email(email)
    supplied_code = str(code or "").strip()

    challenge = _latest_challenge(
        db,
        canonical_email,
    )

    if challenge is None:
        raise CustomerVerificationError(
            "no_challenge",
            "Please request a new verification code.",
        )

    if challenge.used_at is not None:
        raise CustomerVerificationError(
            "used",
            "This verification code has already been used.",
        )

    if _as_utc(challenge.expires_at) < current:
        challenge.used_at = current
        db.commit()

        raise CustomerVerificationError(
            "expired",
            "This verification code has expired.",
        )

    if challenge.attempts >= challenge.max_attempts:
        challenge.used_at = current
        db.commit()

        raise CustomerVerificationError(
            "locked",
            "Too many incorrect attempts. Please request a new code.",
        )

    expected = customer_code_digest(
        secret_key,
        canonical_email,
        supplied_code,
    )

    if not hmac.compare_digest(
        challenge.code_digest,
        expected,
    ):
        challenge.attempts += 1

        if challenge.attempts >= challenge.max_attempts:
            challenge.used_at = current

        db.commit()

        raise CustomerVerificationError(
            "invalid_code",
            "That verification code is not correct.",
        )

    identity = db.scalar(
        select(CustomerIdentity).where(
            CustomerIdentity.email == canonical_email
        )
    )

    if identity is None:
        identity = CustomerIdentity(
            email=canonical_email,
            first_name=challenge.first_name,
            last_name=challenge.last_name,
            verified_at=current,
        )
        db.add(identity)
    else:
        if challenge.first_name:
            identity.first_name = challenge.first_name

        if challenge.last_name:
            identity.last_name = challenge.last_name

        identity.verified_at = current

    challenge.used_at = current

    db.commit()
    db.refresh(identity)

    return identity


def remember_verified_customer(
    session_state,
    identity: CustomerIdentity,
    *,
    now: datetime | None = None,
) -> None:
    """Place only the stable identity ID into the signed browser session."""

    current = _as_utc(now or _now())

    session_state["verified_customer_id"] = str(
        identity.id
    )
    session_state["verified_customer_at"] = (
        current.isoformat()
    )


def clear_verified_customer(session_state) -> None:
    session_state.pop(
        "verified_customer_id",
        None,
    )
    session_state.pop(
        "verified_customer_at",
        None,
    )


def verified_customer_id(
    session_state,
) -> str:
    return str(
        session_state.get(
            "verified_customer_id",
            "",
        )
        or ""
    )
