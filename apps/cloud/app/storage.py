from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError


def spaces_enabled(settings) -> bool:
    return bool(
        settings.spaces_bucket
        and settings.spaces_endpoint
        and settings.spaces_access_key
        and settings.spaces_secret_key
    )


def client(settings):
    return boto3.client(
        "s3",
        region_name=settings.spaces_region or None,
        endpoint_url=settings.spaces_endpoint,
        aws_access_key_id=settings.spaces_access_key,
        aws_secret_access_key=settings.spaces_secret_key,
    )


def upload_bytes(settings, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
    if not spaces_enabled(settings):
        raise RuntimeError("DigitalOcean Spaces storage is not configured")

    client(settings).put_object(
        Bucket=settings.spaces_bucket,
        Key=key,
        Body=content,
        ContentType=content_type,
    )
    return "spaces://" + key


def upload_file(settings, key: str, path: Path, content_type: str = "application/octet-stream") -> str:
    if not spaces_enabled(settings):
        raise RuntimeError("DigitalOcean Spaces storage is not configured")

    with Path(path).open("rb") as handle:
        client(settings).upload_fileobj(
            handle,
            settings.spaces_bucket,
            key,
            ExtraArgs={
                "ContentType": content_type,
            },
        )

    return "spaces://" + key


def exists(settings, location: str) -> bool:
    if location.startswith("spaces://"):
        key = location[len("spaces://"):]
        try:
            client(settings).head_object(
                Bucket=settings.spaces_bucket,
                Key=key,
            )
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    return Path(location).is_file()


def download_to_temp(settings, location: str, suffix: str = "") -> Path:
    if not location.startswith("spaces://"):
        return Path(location)

    key = location[len("spaces://"):]

    fd, filename = tempfile.mkstemp(
        prefix="pirouette-delivery-",
        suffix=suffix,
    )
    os.close(fd)

    target = Path(filename)

    try:
        with target.open("wb") as handle:
            client(settings).download_fileobj(
                settings.spaces_bucket,
                key,
                handle,
            )
    except Exception:
        target.unlink(missing_ok=True)
        raise

    return target


def presigned_get_url(
    settings,
    location: str,
    *,
    expires_seconds: int = 300,
    storage_client=None,
) -> str:
    """Return a short-lived private download URL for a Spaces object.

    ``storage_client`` lets callers signing a batch of objects reuse one boto3
    client. Constructing a fresh botocore client for every gallery thumbnail is
    surprisingly expensive and made large private favourites folders slow to
    render even though signing itself is local.
    """

    if not location.startswith("spaces://"):
        raise ValueError("Presigned URLs require a spaces:// storage location")

    key = location[len("spaces://"):]
    signer = storage_client or client(settings)

    return signer.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.spaces_bucket,
            "Key": key,
        },
        ExpiresIn=expires_seconds,
    )



def presigned_put_url(
    settings,
    key: str,
    *,
    content_type: str = "application/octet-stream",
    expires_seconds: int = 900,
) -> str:
    """Return a short-lived private Spaces PUT URL."""
    if not spaces_enabled(settings):
        raise RuntimeError(
            "DigitalOcean Spaces storage is not configured"
        )

    return client(settings).generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.spaces_bucket,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_seconds,
    )


def delete(settings, location: str) -> None:
    if not location:
        return

    if location.startswith("spaces://"):
        key = location[len("spaces://"):]
        client(settings).delete_object(
            Bucket=settings.spaces_bucket,
            Key=key,
        )
        return

    Path(location).unlink(missing_ok=True)
