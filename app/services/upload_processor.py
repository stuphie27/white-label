from pathlib import Path
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3
from botocore.config import Config


CONFIG_PATH = (
    Path.home()
    / ".config"
    / "stuphie-online"
    / "spaces.env"
)


SYNC_CONFIG_PATH = (
    Path.home()
    / ".config"
    / "stuphie-online"
    / "sync.env"
)

_FOLDER_SYNC_STATE = {}


def load_sync_config():
    if not SYNC_CONFIG_PATH.exists():
        raise RuntimeError(
            f"Stuphie sync configuration not found: {SYNC_CONFIG_PATH}"
        )

    values = {}

    for raw_line in SYNC_CONFIG_PATH.read_text().splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    required = [
        "STUPHIE_SYNC_BASE_URL",
        "STUPHIE_SYNC_API_KEY",
    ]

    missing = [
        key for key in required
        if not values.get(key)
    ]

    if missing:
        raise RuntimeError(
            "Missing Stuphie sync configuration: "
            + ", ".join(missing)
        )

    return values


def sync_json(config, method, path, payload=None, headers=None):
    base_url = config["STUPHIE_SYNC_BASE_URL"].rstrip("/")
    url = base_url + path

    request_headers = {
        "X-Pirouette-Sync-Key": config["STUPHIE_SYNC_API_KEY"],
        "Accept": "application/json",
    }

    if headers:
        request_headers.update(headers)

    data = None

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    elif method in {"POST", "PUT"}:
        data = b""

    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=60,
        ) as response:
            raw = response.read()

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"Cloud sync HTTP {exc.code}: {body[:500]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cloud sync connection failed: {exc}"
        ) from exc

    if not raw:
        return {}

    return json.loads(raw.decode("utf-8"))


def sync_folder_manifest(event, config):
    source_folder = Path(event["source_folder"])

    if not source_folder.exists():
        raise RuntimeError(
            f"Source folder is unavailable: {source_folder}"
        )

    gallery_ref = str(event["public_slug"])
    now = time.monotonic()
    state = _FOLDER_SYNC_STATE.get(gallery_ref)

    # Avoid walking a large event tree for every individual photograph.
    if state and now - state["checked_at"] < 30:
        return

    folders = []

    for path in source_folder.rglob("*"):
        if not path.is_dir():
            continue

        relative = str(
            path.relative_to(source_folder)
        ).replace("\\", "/").strip("/")

        if relative:
            folders.append(relative)

    folders.sort()
    signature = tuple(folders)

    if not state or state.get("signature") != signature:
        quoted_gallery = urllib.parse.quote(
            gallery_ref,
            safe="",
        )

        sync_json(
            config,
            "PUT",
            (
                "/api/sync/v1/galleries/"
                f"{quoted_gallery}/folders"
            ),
            {
                "folders": [
                    {
                        "path": folder,
                        "media_kind": "photos",
                    }
                    for folder in folders
                ]
            },
        )

    _FOLDER_SYNC_STATE[gallery_ref] = {
        "checked_at": now,
        "signature": signature,
    }


def sync_preview_to_cloud(event, job, preview_path):
    config = load_sync_config()

    sync_folder_manifest(
        event,
        config,
    )

    gallery_ref = str(event["public_slug"])
    asset_ref = f"photo-{int(job['photo_id']):08d}"

    quoted_gallery = urllib.parse.quote(
        gallery_ref,
        safe="",
    )
    quoted_asset = urllib.parse.quote(
        asset_ref,
        safe="",
    )

    filename = str(job["original_filename"])
    relative_folder = str(
        job["relative_folder"] or ""
    ).replace("\\", "/").strip("/")

    content_type = "image/jpeg"
    preview_bytes = preview_path.read_bytes()

    prepared = sync_json(
        config,
        "POST",
        (
            "/api/sync/v1/galleries/"
            f"{quoted_gallery}/assets/"
            f"{quoted_asset}/direct-upload"
        ),
        headers={
            "X-Pirouette-Filename": filename,
            "X-Pirouette-Content-Type": content_type,
        },
    )

    upload_url = prepared.get("upload_url")

    if not upload_url:
        raise RuntimeError(
            "Cloud did not return a direct upload URL."
        )

    upload_request = urllib.request.Request(
        upload_url,
        data=preview_bytes,
        headers={
            "Content-Type": content_type,
        },
        method="PUT",
    )

    try:
        with urllib.request.urlopen(
            upload_request,
            timeout=120,
        ) as response:
            response.read()

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"Preview storage upload HTTP {exc.code}: {body[:500]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Preview storage upload failed: {exc}"
        ) from exc

    completed = sync_json(
        config,
        "POST",
        (
            "/api/sync/v1/galleries/"
            f"{quoted_gallery}/assets/"
            f"{quoted_asset}/direct-upload/complete"
        ),
        headers={
            "X-Pirouette-Filename": filename,
            "X-Pirouette-Content-Type": content_type,
            "X-Pirouette-Folder-Path": relative_folder,
            "X-Pirouette-Media-Kind": "photos",
            "X-Pirouette-Size-Bytes": str(
                len(preview_bytes)
            ),
        },
    )

    if completed.get("status") != "ready":
        raise RuntimeError(
            "Cloud preview did not become ready."
        )

    if str(completed.get("source_ref") or "") != asset_ref:
        raise RuntimeError(
            "Cloud asset reference verification failed."
        )

    verified = sync_json(
        config,
        "GET",
        (
            "/api/sync/v1/galleries/"
            f"{quoted_gallery}/assets/"
            f"{quoted_asset}/metadata"
        ),
    )

    if str(verified.get("source_ref") or "") != asset_ref:
        raise RuntimeError(
            "Cloud post-upload metadata verification failed."
        )

    if str(verified.get("status") or "") != "ready":
        raise RuntimeError(
            "Cloud post-upload asset is not ready."
        )

    if not bool(
        verified.get("public_preview_available")
    ):
        raise RuntimeError(
            "Cloud post-upload preview is unavailable."
        )

    cloud_folder = str(
        completed.get("folder_path") or ""
    ).replace("\\", "/").strip("/")

    if cloud_folder != relative_folder:
        raise RuntimeError(
            "Cloud folder verification failed: "
            f"expected {relative_folder!r}, "
            f"received {cloud_folder!r}."
        )

    if str(completed.get("media_kind") or "") != "photos":
        raise RuntimeError(
            "Cloud media type verification failed."
        )

    if str(verified.get("media_kind") or "") != "photos":
        raise RuntimeError(
            "Cloud post-upload media type verification failed."
        )

    verified_folder = str(
        verified.get("folder_path") or ""
    ).replace("\\", "/").strip("/")

    if verified_folder != relative_folder:
        raise RuntimeError(
            "Cloud post-upload folder verification failed: "
            f"expected {relative_folder!r}, "
            f"received {verified_folder!r}."
        )

    if not bool(
        completed.get("public_preview_available")
    ):
        raise RuntimeError(
            "Cloud preview verification failed: "
            "public preview is unavailable."
        )

    cloud_size = int(
        completed.get("size_bytes") or 0
    )

    verified_size = int(
        verified.get("size_bytes") or 0
    )

    if verified_size != len(preview_bytes):
        raise RuntimeError(
            "Cloud post-upload size verification failed: "
            f"expected {len(preview_bytes)}, "
            f"received {verified_size}."
        )

    if cloud_size != len(preview_bytes):
        raise RuntimeError(
            "Cloud preview size verification failed: "
            f"expected {len(preview_bytes)}, "
            f"received {cloud_size}."
        )

    return {
        "asset_ref": asset_ref,
        "cloud_size": cloud_size,
        "folder_path": cloud_folder,
        "verified": True,
    }


def load_spaces_config():
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"DigitalOcean Spaces configuration not found: {CONFIG_PATH}"
        )

    values = {}

    for raw_line in CONFIG_PATH.read_text().splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    required = [
        "STUPHIE_SPACES_KEY",
        "STUPHIE_SPACES_SECRET",
        "STUPHIE_SPACES_BUCKET",
        "STUPHIE_SPACES_REGION",
        "STUPHIE_SPACES_ENDPOINT",
    ]

    missing = [
        key for key in required
        if not values.get(key)
    ]

    if missing:
        raise RuntimeError(
            "Missing Spaces configuration: "
            + ", ".join(missing)
        )

    return values


def spaces_client(config):
    return boto3.client(
        "s3",
        region_name=config["STUPHIE_SPACES_REGION"],
        endpoint_url=config["STUPHIE_SPACES_ENDPOINT"],
        aws_access_key_id=config["STUPHIE_SPACES_KEY"],
        aws_secret_access_key=config["STUPHIE_SPACES_SECRET"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
            retries={
                "max_attempts": 5,
                "mode": "standard",
            },
            connect_timeout=20,
            read_timeout=60,
            tcp_keepalive=True,
        ),
    )


def process_next_upload(event_db_path: str):
    event_db = Path(event_db_path)

    if not event_db.exists():
        raise RuntimeError(
            f"Event database not found: {event_db}"
        )

    config = load_spaces_config()
    client = spaces_client(config)
    bucket = config["STUPHIE_SPACES_BUCKET"]

    conn = sqlite3.connect(event_db, timeout=30)
    conn.row_factory = sqlite3.Row

    event = conn.execute(
        """
        SELECT *
        FROM event_info
        WHERE id = 1
        """
    ).fetchone()

    if not event:
        conn.close()
        raise RuntimeError(
            "Event information is missing."
        )

    # Claim exactly one upload job atomically. BEGIN IMMEDIATE
    # prevents concurrent upload workers taking the same photo.
    conn.execute("BEGIN IMMEDIATE")

    job = conn.execute(
        """
        SELECT
            j.id AS job_id,
            j.photo_id,
            p.original_filename,
            p.relative_folder,
            p.preview_path,
            p.preview_status
        FROM processing_jobs j
        JOIN photos p
          ON p.id = j.photo_id
        WHERE j.job_type = 'upload_preview'
          AND j.status = 'pending'
          AND p.preview_status = 'ready'
          AND p.preview_path IS NOT NULL
          AND p.preview_path != ''
        ORDER BY j.id
        LIMIT 1
        """
    ).fetchone()

    if not job:
        conn.rollback()
        conn.close()
        return None

    preview_path = Path(
        job["preview_path"] or ""
    )

    if (
        job["preview_status"] != "ready"
        or not preview_path.exists()
    ):
        conn.close()
        raise RuntimeError(
            "Preview is not ready or local preview file is missing."
        )

    remote_key = (
        f"previews/"
        f"{event['brand_id']}/"
        f"{event['public_slug']}/"
        f"{job['photo_id']:08d}.jpg"
    )

    conn.execute(
        """
        UPDATE processing_jobs
        SET
            status = 'processing',
            attempts = attempts + 1,
            started_at = CURRENT_TIMESTAMP,
            last_error = NULL
        WHERE id = ?
        """,
        (job["job_id"],),
    )

    conn.execute(
        """
        UPDATE photos
        SET
            upload_status = 'uploading',
            last_error = NULL
        WHERE id = ?
        """,
        (job["photo_id"],),
    )

    conn.commit()

    try:
        preview_bytes = preview_path.read_bytes()

        client.put_object(
            Bucket=bucket,
            Key=remote_key,
            Body=preview_bytes,
            ContentType="image/jpeg",
            CacheControl="public, max-age=31536000",
        )

        info = client.head_object(
            Bucket=bucket,
            Key=remote_key,
        )

        cloud_result = sync_preview_to_cloud(
            event,
            job,
            preview_path,
        )

        conn.execute(
            """
            UPDATE photos
            SET
                upload_status = 'uploaded',
                remote_preview_key = ?,
                uploaded_at = CURRENT_TIMESTAMP,
                last_error = NULL
            WHERE id = ?
            """,
            (
                remote_key,
                job["photo_id"],
            ),
        )

        conn.execute(
            """
            UPDATE processing_jobs
            SET
                status = 'completed',
                completed_at = CURRENT_TIMESTAMP,
                last_error = NULL
            WHERE id = ?
            """,
            (job["job_id"],),
        )

        conn.commit()

        return {
            "photo_id": job["photo_id"],
            "remote_key": remote_key,
            "size": info["ContentLength"],
            "cloud_asset_ref": cloud_result["asset_ref"],
        }

    except Exception as exc:
        conn.execute(
            """
            UPDATE photos
            SET
                upload_status = 'failed',
                retry_count = retry_count + 1,
                last_error = ?
            WHERE id = ?
            """,
            (
                str(exc),
                job["photo_id"],
            ),
        )

        conn.execute(
            """
            UPDATE processing_jobs
            SET
                status = 'failed',
                completed_at = CURRENT_TIMESTAMP,
                last_error = ?
            WHERE id = ?
            """,
            (
                str(exc),
                job["job_id"],
            ),
        )

        conn.commit()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python -m app.services.upload_processor "
            "/path/to/event.db"
        )
        raise SystemExit(1)

    try:
        result = process_next_upload(
            sys.argv[1]
        )
    except Exception as exc:
        print("")
        print("UPLOAD FAILED")
        print(exc)
        raise SystemExit(1)

    if result is None:
        print("")
        print("No pending preview uploads.")
        raise SystemExit(0)

    print("")
    print("===== STUPHIE CLOUD UPLOAD =====")
    print(f"Photo ID   : {result['photo_id']}")
    print(f"Remote Key : {result['remote_key']}")
    print(f"Size       : {result['size']} bytes")
    print("Upload     : COMPLETED")
    print("================================")
