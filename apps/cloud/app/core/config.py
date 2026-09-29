from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import EmailStr, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_version() -> str:
    version_file = PROJECT_ROOT / "VERSION"
    return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "0.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PIROUETTE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    environment: str = Field(default="development")
    public_base_url: str = Field(default="http://127.0.0.1:8000")
    allowed_hosts: str = Field(default="")
    secret_key: str = Field(default="development-only-secret-change-before-production")
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    log_level: str = Field(default="INFO")
    version: str = Field(default_factory=read_version)

    admin_email: EmailStr = Field(default="admin@example.com")
    admin_password_hash: str = Field(default="")
    super_admin_emails: str = Field(
        default=(
            "accounting@sophiesphotography.co.uk,"
            "sophieperren020521@gmail.com,"
            "amy@sophiesphotography.co.uk"
        )
    )

    @property
    def trusted_host_list(self) -> list[str]:
        """Hosts accepted by Starlette TrustedHostMiddleware.

        Production automatically trusts the hostname configured in
        PIROUETTE_PUBLIC_BASE_URL. Additional deployment hostnames may be
        supplied explicitly through PIROUETTE_ALLOWED_HOSTS.

        Development/test remains permissive so local work is not disrupted.
        """
        if self.environment in {"development", "test"}:
            return ["*"]

        parsed = urlparse(self.public_base_url)
        hosts: list[str] = []

        if parsed.hostname:
            hosts.append(parsed.hostname.lower())

        for value in self.allowed_hosts.split(","):
            host = value.strip().lower()
            if host and host not in hosts:
                hosts.append(host)

        # Safe local/platform health-check hostnames.
        for host in ("localhost", "127.0.0.1"):
            if host not in hosts:
                hosts.append(host)

        return hosts

    @property
    def super_admin_email_set(self) -> set[str]:
        return {
            email.strip().lower()
            for email in self.super_admin_emails.split(",")
            if email.strip()
        }
    payroll_otp_allowed_emails: str = Field(default="")
    payroll_otp_ttl_seconds: int = Field(default=600, ge=120, le=1800)
    payroll_otp_unlock_seconds: int = Field(default=1800, ge=300, le=7200)

    @property
    def payroll_otp_allowed_email_set(self) -> set[str]:
        return {
            email.strip().lower()
            for email in self.payroll_otp_allowed_emails.split(",")
            if email.strip()
        }

    session_max_age_seconds: int = Field(default=28800, ge=900, le=86400)
    login_max_attempts: int = Field(default=5, ge=3, le=20)
    login_window_seconds: int = Field(default=900, ge=60, le=3600)
    sync_api_key: str = Field(default="")
    stuphie_base_url: str = Field(default="https://stuphie.co.uk")
    stuphie_sync_api_key: str = Field(default="")
    stuphie_event_master_enabled: bool = Field(default=False)
    temporary_media_dir: str = Field(default="/tmp/pirouette-cloud-media")
    max_live_asset_bytes: int = Field(default=15000000, ge=100000, le=50000000)
    max_delivery_asset_bytes: int = Field(default=100000000, ge=1000000, le=500000000)
    delivery_dir: str = Field(default="/tmp/pirouette-cloud-deliveries")
    spaces_bucket: str = Field(default="")
    spaces_region: str = Field(default="")
    spaces_endpoint: str = Field(default="")
    spaces_access_key: str = Field(default="")
    spaces_secret_key: str = Field(default="")
    delivery_retention_days: int = Field(default=5, ge=1, le=30)
    delivery_reminder_hours: int = Field(default=48, ge=1, le=120)  # legacy display value; reminder is sent on day 3
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_use_tls: bool = Field(default=True)
    smtp_from_email: str = Field(default="photos@sophiesphotography.co.uk")
    smtp_from_name: str = Field(default="Sophie’s Photography")
    delivery_admin_email: str = Field(default="photos@sophiesphotography.co.uk")
    sumup_api_key: str = Field(default="")
    sumup_merchant_code: str = Field(default="")
    sumup_currency: str = Field(default="GBP")
    sumup_api_base: str = Field(default="https://api.sumup.com")
    sumup_timeout_seconds: int = Field(default=15, ge=3, le=60)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"development", "staging", "production", "test"}:
            raise ValueError("environment must be development, staging, production, or test")
        return value

    @field_validator("database_url")
    @classmethod
    def normalise_database_url(cls, value: str) -> str:
        value = value.strip()
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://"):]
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://"):]
        return value

    def validate_for_startup(self) -> None:
        if self.environment not in {"staging", "production"}:
            return
        if len(self.secret_key) < 40 or "development" in self.secret_key.lower() or "replace" in self.secret_key.lower():
            raise RuntimeError("PIROUETTE_SECRET_KEY must be a strong random value in staging/production")
        parsed = urlparse(self.public_base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("PIROUETTE_PUBLIC_BASE_URL must be a valid HTTPS URL in staging/production")
        if str(self.admin_email).lower() == "admin@example.com":
            raise RuntimeError("PIROUETTE_ADMIN_EMAIL must be configured in staging/production")
        if not self.admin_password_hash.startswith("$argon2"):
            raise RuntimeError("PIROUETTE_ADMIN_PASSWORD_HASH must contain an Argon2 password hash")

        payroll_emails = self.payroll_otp_allowed_email_set

        if not payroll_emails:
            raise RuntimeError(
                "PIROUETTE_PAYROLL_OTP_ALLOWED_EMAILS must be configured "
                "in staging/production"
            )

        unknown_payroll_emails = (
            payroll_emails - self.super_admin_email_set
        )

        if unknown_payroll_emails:
            raise RuntimeError(
                "PIROUETTE_PAYROLL_OTP_ALLOWED_EMAILS may only contain "
                "configured Super Admin email addresses"
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_for_startup()
    return settings
