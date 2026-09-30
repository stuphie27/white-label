from pathlib import Path
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime

from app.services.scanner import scan_event
from app.services.preview_processor import process_next_preview
from app.services.upload_processor import (
    process_next_upload,
    load_sync_config,
    sync_json,
)
from app.services.delivery_processor import process_next_delivery


SCAN_INTERVAL_SECONDS = 5
IDLE_SLEEP_SECONDS = 0.5
UPLOAD_RETRY_DELAY_SECONDS = 30
MAX_UPLOAD_RETRIES = 5
DELIVERY_POLL_INTERVAL_SECONDS = 10

# Re-check every successful cloud upload after it has had time
# to settle. If the cloud asset has disappeared, automatically
# put the photo back into the upload queue.
POST_UPLOAD_VERIFY_DELAY_SECONDS = 15

# Periodically compare every CURRENT source photograph on disk
# against the live cloud gallery. Missing cloud assets are
# automatically returned to the upload queue.
FULL_RECONCILE_INTERVAL_SECONDS = 60


def requeue_retryable_uploads(event_db_path: str):
    conn = sqlite3.connect(event_db_path)

    conn.execute(
        """
        UPDATE processing_jobs
        SET
            status = 'pending',
            started_at = NULL,
            completed_at = NULL,
            last_error = NULL
        WHERE job_type = 'upload_preview'
          AND status = 'failed'
          AND photo_id IN (
              SELECT id
              FROM photos
              WHERE preview_status = 'ready'
                AND upload_status = 'failed'
                AND retry_count < ?
          )
          AND completed_at IS NOT NULL
          AND completed_at <= datetime(
              'now',
              ?
          )
        """,
        (
            MAX_UPLOAD_RETRIES,
            f"-{UPLOAD_RETRY_DELAY_SECONDS} seconds",
        ),
    )

    conn.execute(
        """
        UPDATE photos
        SET
            upload_status = 'pending',
            last_error = NULL
        WHERE id IN (
            SELECT photo_id
            FROM processing_jobs
            WHERE job_type = 'upload_preview'
              AND status = 'pending'
        )
          AND preview_status = 'ready'
          AND upload_status = 'failed'
          AND retry_count < ?
        """,
        (MAX_UPLOAD_RETRIES,),
    )

    conn.commit()
    conn.close()


def verify_uploaded_photo(event_db_path: str, photo_id: int) -> bool:
    conn = sqlite3.connect(event_db_path)
    conn.row_factory = sqlite3.Row

    try:
        event = conn.execute(
            """
            SELECT public_slug
            FROM event_info
            WHERE id = 1
            """
        ).fetchone()

        photo = conn.execute(
            """
            SELECT id, upload_status
            FROM photos
            WHERE id = ?
            """,
            (photo_id,),
        ).fetchone()

        if not event or not photo:
            return True

        if photo["upload_status"] != "uploaded":
            return True

        gallery_ref = str(event["public_slug"])
        asset_ref = f"photo-{int(photo_id):08d}"

        config = load_sync_config()

        result = sync_json(
            config,
            "GET",
            (
                "/api/sync/v1/galleries/"
                f"{urllib.parse.quote(gallery_ref, safe='')}"
                "/assets/"
                f"{urllib.parse.quote(asset_ref, safe='')}"
                "/metadata"
            ),
        )

        if (
            str(result.get("source_ref") or "") == asset_ref
            and str(result.get("status") or "") == "ready"
            and bool(result.get("public_preview_available"))
        ):
            return True

        raise RuntimeError(
            "Cloud asset did not pass delayed verification."
        )

    except Exception as exc:
        conn.execute(
            """
            UPDATE photos
            SET
                upload_status = 'pending',
                uploaded_at = NULL,
                remote_preview_key = NULL,
                last_error = ?
            WHERE id = ?
            """,
            (
                f"Cloud reconciliation requeued upload: {exc}",
                photo_id,
            ),
        )

        job = conn.execute(
            """
            SELECT id
            FROM processing_jobs
            WHERE photo_id = ?
              AND job_type = 'upload_preview'
            ORDER BY id DESC
            LIMIT 1
            """,
            (photo_id,),
        ).fetchone()

        if job:
            conn.execute(
                """
                UPDATE processing_jobs
                SET
                    status = 'pending',
                    started_at = NULL,
                    completed_at = NULL,
                    last_error = NULL
                WHERE id = ?
                """,
                (job["id"],),
            )
        else:
            conn.execute(
                """
                INSERT INTO processing_jobs (
                    photo_id,
                    job_type,
                    status
                )
                VALUES (?, 'upload_preview', 'pending')
                """,
                (photo_id,),
            )

        conn.commit()

        print(
            "CLOUD REQUEUE "
            f"photo={photo_id} "
            f"reason={exc}",
            flush=True,
        )

        return False

    finally:
        conn.close()


def reconcile_current_source_photos(event_db_path: str):
    conn = sqlite3.connect(event_db_path)
    conn.row_factory = sqlite3.Row

    try:
        event = conn.execute(
            """
            SELECT public_slug
            FROM event_info
            WHERE id = 1
            """
        ).fetchone()

        if not event:
            raise RuntimeError(
                "Event information is missing."
            )

        rows = conn.execute(
            """
            SELECT
                id,
                original_path,
                original_filename,
                preview_status,
                upload_status
            FROM photos
            ORDER BY id
            """
        ).fetchall()

        # Only files that STILL EXIST on the event drive are current.
        current_rows = [
            row
            for row in rows
            if Path(str(row["original_path"] or "")).is_file()
        ]

        config = load_sync_config()
        gallery_ref = str(event["public_slug"])

        health = sync_json(
            config,
            "GET",
            (
                "/api/sync/v1/galleries/"
                f"{urllib.parse.quote(gallery_ref, safe='')}"
                "/health"
            ),
        )

        available = {
            str(ref)
            for ref in health.get(
                "available_source_refs",
                [],
            )
        }

        expected = {
            f"photo-{int(row['id']):08d}": row
            for row in current_rows
        }

        verified_count = sum(
            1
            for ref in expected
            if ref in available
        )

        requeued = []

        for ref, row in expected.items():
            if row["upload_status"] != "uploaded":
                continue

            if ref in available:
                continue

            photo_id = int(row["id"])

            conn.execute(
                """
                UPDATE photos
                SET
                    upload_status = 'pending',
                    uploaded_at = NULL,
                    remote_preview_key = NULL,
                    last_error = ?
                WHERE id = ?
                """,
                (
                    "Cloud reconciliation found "
                    "the current photo missing online; "
                    "upload automatically requeued.",
                    photo_id,
                ),
            )

            job = conn.execute(
                """
                SELECT id
                FROM processing_jobs
                WHERE photo_id = ?
                  AND job_type = 'upload_preview'
                ORDER BY id DESC
                LIMIT 1
                """,
                (photo_id,),
            ).fetchone()

            if job:
                conn.execute(
                    """
                    UPDATE processing_jobs
                    SET
                        status = 'pending',
                        started_at = NULL,
                        completed_at = NULL,
                        last_error = NULL
                    WHERE id = ?
                    """,
                    (job["id"],),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO processing_jobs (
                        photo_id,
                        job_type,
                        status
                    )
                    VALUES (?, 'upload_preview', 'pending')
                    """,
                    (photo_id,),
                )

            requeued.append(photo_id)

        conn.commit()

        print(
            "CLOUD RECONCILE "
            f"current={len(current_rows)} "
            f"verified={verified_count} "
            f"requeued={len(requeued)}",
            flush=True,
        )

        if requeued:
            print(
                "CLOUD REQUEUE BATCH photos="
                + ",".join(str(photo_id) for photo_id in requeued),
                flush=True,
            )

        return {
            "current": len(current_rows),
            "verified": verified_count,
            "requeued": len(requeued),
        }

    finally:
        conn.close()


def queue_status(event_db_path: str):
    conn = sqlite3.connect(event_db_path)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        """
        SELECT
            SUM(
                CASE
                    WHEN job_type = 'create_preview'
                     AND status = 'pending'
                    THEN 1 ELSE 0
                END
            ) AS previews_pending,

            SUM(
                CASE
                    WHEN job_type = 'upload_preview'
                     AND status = 'pending'
                    THEN 1 ELSE 0
                END
            ) AS uploads_pending,

            SUM(
                CASE
                    WHEN job_type = 'upload_preview'
                     AND status = 'failed'
                    THEN 1 ELSE 0
                END
            ) AS uploads_failed
        FROM processing_jobs
        """
    ).fetchone()

    conn.close()

    return {
        "previews_pending": row["previews_pending"] or 0,
        "uploads_pending": row["uploads_pending"] or 0,
        "uploads_failed": row["uploads_failed"] or 0,
    }


def run_worker(event_db_path: str):
    event_db = Path(event_db_path)

    if not event_db.exists():
        raise RuntimeError(
            f"Event database not found: {event_db}"
        )

    print("")
    print("====================================")
    print("STUPHIE EVENT WORKER")
    print("====================================")
    print("Database :", event_db)
    print("Started  :", datetime.now().isoformat(timespec="seconds"))
    print("Mode     : AUTO")
    print("====================================")
    print("")

    last_scan = 0.0
    last_delivery_poll = 0.0
    last_full_reconcile = 0.0
    cloud_verify_queue: list[tuple[float, int]] = []

    while True:
        did_work = False
        now = time.monotonic()

        if now - last_scan >= SCAN_INTERVAL_SECONDS:
            try:
                scan_event(str(event_db))
            except Exception as exc:
                print(
                    "SCAN WARNING:",
                    exc,
                    flush=True,
                )

            last_scan = now

        try:
            requeue_retryable_uploads(
                str(event_db)
            )
        except Exception as exc:
            print(
                "RETRY WARNING:",
                exc,
                flush=True,
            )

        try:
            result = process_next_preview(
                str(event_db)
            )

            if result:
                did_work = True
                print(
                    f"PREVIEW READY  photo={result['photo_id']}",
                    flush=True,
                )

        except Exception as exc:
            print(
                "PREVIEW WARNING:",
                exc,
                flush=True,
            )

        try:
            result = process_next_upload(
                str(event_db)
            )

            if result:
                did_work = True

                photo_id = int(result["photo_id"])

                cloud_verify_queue.append(
                    (
                        time.monotonic()
                        + POST_UPLOAD_VERIFY_DELAY_SECONDS,
                        photo_id,
                    )
                )

                print(
                    "CLOUD UPLOADED "
                    f"photo={photo_id} "
                    f"size={result['size']}",
                    flush=True,
                )

        except Exception as exc:
            print(
                "UPLOAD WARNING:",
                exc,
                flush=True,
            )

        due_verifications = [
            item
            for item in cloud_verify_queue
            if item[0] <= now
        ]

        cloud_verify_queue = [
            item
            for item in cloud_verify_queue
            if item[0] > now
        ]

        for _due_at, photo_id in due_verifications:
            try:
                if verify_uploaded_photo(
                    str(event_db),
                    photo_id,
                ):
                    print(
                        f"CLOUD VERIFIED photo={photo_id}",
                        flush=True,
                    )
            except Exception as exc:
                print(
                    "VERIFY WARNING:",
                    exc,
                    flush=True,
                )

        if (
            now - last_full_reconcile
            >= FULL_RECONCILE_INTERVAL_SECONDS
        ):
            try:
                reconciliation = reconcile_current_source_photos(
                    str(event_db)
                )

                if reconciliation["requeued"]:
                    did_work = True

            except Exception as exc:
                print(
                    "RECONCILE WARNING:",
                    exc,
                    flush=True,
                )

            last_full_reconcile = now

        if now - last_delivery_poll >= DELIVERY_POLL_INTERVAL_SECONDS:
            try:
                delivery = process_next_delivery(
                    str(event_db)
                )

                if delivery:
                    did_work = True
                    print(
                        "DELIVERY READY "
                        f"order={delivery['order_reference']} "
                        f"type={delivery['product_type']} "
                        f"files={delivery['files']} "
                        f"status={delivery['status']}",
                        flush=True,
                    )

            except Exception as exc:
                print(
                    "DELIVERY WARNING:",
                    exc,
                    flush=True,
                )

            last_delivery_poll = now

        if not did_work:
            time.sleep(IDLE_SLEEP_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python -m app.services.event_worker "
            "/path/to/event.db"
        )
        raise SystemExit(1)

    try:
        run_worker(sys.argv[1])

    except KeyboardInterrupt:
        print("")
        print("Stuphie worker stopped.")

    except Exception as exc:
        print("")
        print("WORKER FAILED")
        print(exc)
        raise SystemExit(1)
