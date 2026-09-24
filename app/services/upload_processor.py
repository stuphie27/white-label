from pathlib import Path
import sqlite3
import sys

import boto3
from botocore.config import Config


CONFIG_PATH = (
    Path.home()
    / ".config"
    / "stuphie-online"
    / "spaces.env"
)


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

    conn = sqlite3.connect(event_db)
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

    job = conn.execute(
        """
        SELECT
            j.id AS job_id,
            j.photo_id,
            p.preview_path,
            p.preview_status
        FROM processing_jobs j
        JOIN photos p
          ON p.id = j.photo_id
        WHERE j.job_type = 'upload_preview'
          AND j.status = 'pending'
        ORDER BY j.id
        LIMIT 1
        """
    ).fetchone()

    if not job:
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
