from __future__ import annotations

import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

LOGGER = logging.getLogger("pirouette.auth")
PASSWORD_HASH = PasswordHasher()
DUMMY_HASH = PASSWORD_HASH.hash("pirouette-dummy-password")


@dataclass(frozen=True)
class LoginDecision:
    allowed: bool
    retry_after_seconds: int = 0


class LoginRateLimiter:
    """Small in-process limiter suitable for the single-container foundation release."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> LoginDecision:
        now = time.monotonic()
        with self._lock:
            bucket = self._attempts[key]
            while bucket and now - bucket[0] >= self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.max_attempts:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
                return LoginDecision(False, retry_after)
            return LoginDecision(True)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._attempts[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


def verify_admin_credentials(
    email: str,
    password: str,
    allowed_emails: set[str],
    password_hash: str,
) -> bool:
    normalised_email = email.strip().lower()

    email_matches = any(
        hmac.compare_digest(normalised_email, candidate)
        for candidate in allowed_emails
    )

    if not email_matches or not password_hash:
        # Perform a dummy hash verification path to reduce timing differences.
        try:
            PASSWORD_HASH.verify(DUMMY_HASH, password)
        except Exception:
            pass
        return False

    try:
        return PASSWORD_HASH.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False
    except InvalidHashError:
        LOGGER.exception(
            "Password verification failed because the configured hash is invalid"
        )
        return False


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_staff_password(password: str) -> str:
    """Create an Argon2 hash for an individual staff password."""
    return PASSWORD_HASH.hash(password)


def verify_staff_password(
    password_hash: str,
    password: str,
) -> bool:
    """
    Verify an individual staff password.

    A dummy verification path is retained when no usable hash exists
    to reduce observable timing differences.
    """
    if not password_hash:
        try:
            PASSWORD_HASH.verify(DUMMY_HASH, password)
        except Exception:
            pass
        return False

    try:
        return PASSWORD_HASH.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
