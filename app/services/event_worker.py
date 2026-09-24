from pathlib import Path
import sqlite3
import sys
import time
from datetime import datetime

from app.services.scanner import scan_event
from app.services.preview_processor import process_next_preview
from app.services.upload_processor import process_next_upload


SCAN_INTERVAL_SECONDS = 5
IDLE_SLEEP_SECONDS = 0.5
UPLOAD_RETRY_DELAY_SECONDS = 30
MAX_UPLOAD_RETRIES = 5


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
                print(
                    "CLOUD UPLOADED "
                    f"photo={result['photo_id']} "
                    f"size={result['size']}",
                    flush=True,
                )

        except Exception as exc:
            print(
                "UPLOAD WARNING:",
                exc,
                flush=True,
            )

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
