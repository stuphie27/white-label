from __future__ import annotations

import os

from argon2 import PasswordHasher

os.environ["PIROUETTE_ENVIRONMENT"] = "test"
os.environ["PIROUETTE_SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-session-signing"
os.environ["PIROUETTE_ADMIN_EMAIL"] = "admin@sophies.photography"
os.environ["PIROUETTE_ADMIN_PASSWORD_HASH"] = PasswordHasher().hash("Correct-Horse-Battery-2026")
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["PIROUETTE_SYNC_API_KEY"] = "test-sync-api-key-2026"
