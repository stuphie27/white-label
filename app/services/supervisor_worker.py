from pathlib import Path
import sqlite3
import time
from datetime import datetime

from app.services.scanner import scan_event
from app.services.preview_processor import process_next_preview
from app.services.upload_processor import process_next_upload


BASE_DIR = Path(__file__).resolve().parent.parent.parent
MASTER_DB = BASE_DIR / "data" / "stuphie_online.db"

LOOP_SLEEP_SECONDS = 1
SCAN_INTERVAL_SECONDS = 5
UPLOAD_RETRY_DELAY_SECONDS = 30
MAX_UPLOAD_RETRIES = 5


def load_enabled_events():
    conn = sqlite3.connect(MASTER_DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            id,
            event_name,
            brand_id,
            public_slug,
            event_root
        FROM events
        WHERE processing_enabled = 1
        ORDER BY event_date, id
        """
    ).fetchall()

    conn.close()
    return rows


def event_database(event):
    event_root = event["event_root"]

    if not event_root:
        return None

    return (
        Path(event_root)
        / "event-data"
        / "event.db"
    )


def requeue_retryable_uploads(event_db):
    conn = sqlite3.connect(event_db)

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


def process_event(event, do_scan):
    event_db = event_database(event)

    if not event_db or not event_db.exists():
        return False

    did_work = False

    if do_scan:
        try:
            scan_event(str(event_db))
        except Exception as exc:
            print(
                f"[{event['event_name']}] SCAN WARNING: {exc}",
                flush=True,
            )

    try:
        requeue_retryable_uploads(event_db)
    except Exception as exc:
        print(
            f"[{event['event_name']}] RETRY WARNING: {exc}",
            flush=True,
        )

    try:
        result = process_next_preview(str(event_db))

        if result:
            did_work = True

            print(
                f"[{event['event_name']}] "
                f"PREVIEW READY photo={result['photo_id']}",
                flush=True,
            )

    except Exception as exc:
        print(
            f"[{event['event_name']}] "
            f"PREVIEW WARNING: {exc}",
            flush=True,
        )

    try:
        result = process_next_upload(str(event_db))

        if result:
            did_work = True

            print(
                f"[{event['event_name']}] "
                f"CLOUD UPLOADED "
                f"photo={result['photo_id']} "
                f"size={result['size']}",
                flush=True,
            )

    except Exception as exc:
        print(
            f"[{event['event_name']}] "
            f"UPLOAD WARNING: {exc}",
            flush=True,
        )

    return did_work


def run_supervisor():
    print("")
    print("====================================")
    print("STUPHIE MULTI-EVENT SUPERVISOR")
    print("====================================")
    print(
        "Started :",
        datetime.now().isoformat(timespec="seconds"),
    )
    print("Mode    : AUTO")
    print("====================================")
    print("")

    last_scan = 0.0

    while True:
        now = time.monotonic()

        do_scan = (
            now - last_scan
            >= SCAN_INTERVAL_SECONDS
        )

        try:
            events = load_enabled_events()
        except Exception as exc:
            print(
                "MASTER DATABASE WARNING:",
                exc,
                flush=True,
            )
            time.sleep(5)
            continue

        did_work = False

        for event in events:
            try:
                if process_event(
                    event,
                    do_scan,
                ):
                    did_work = True

            except Exception as exc:
                print(
                    f"[{event['event_name']}] "
                    f"WORKER WARNING: {exc}",
                    flush=True,
                )

        if do_scan:
            last_scan = now

        if not did_work:
            time.sleep(LOOP_SLEEP_SECONDS)


if __name__ == "__main__":
    try:
        run_supervisor()

    except KeyboardInterrupt:
        print("")
        print("Stuphie supervisor stopped.")
