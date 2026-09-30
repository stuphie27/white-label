from pathlib import Path
import sqlite3
import sys
import time

from PIL import Image, UnidentifiedImageError


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".heic",
}

# A file copied from an SD card can appear in Finder before the copy
# has finished. Do not queue a new/changed photograph until its size
# and modification time have remained unchanged for this long.
FILE_SETTLE_SECONDS = 8

_FILE_SETTLE_STATE = {}


def file_is_stable(path: Path, stat) -> bool:
    key = str(path)
    signature = (
        int(stat.st_size),
        float(stat.st_mtime),
    )
    now = time.monotonic()

    previous = _FILE_SETTLE_STATE.get(key)

    if not previous or previous["signature"] != signature:
        _FILE_SETTLE_STATE[key] = {
            "signature": signature,
            "stable_since": now,
        }
        return False

    return (
        now - previous["stable_since"]
        >= FILE_SETTLE_SECONDS
    )


def image_is_complete(path: Path) -> bool:
    # Finder/macOS may expose a JPG before the SD-card copy has
    # completely finished. Pillow verify() checks the image structure
    # without decoding the full photograph into memory.
    try:
        with Image.open(path) as image:
            image.verify()
        return True

    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return False


def scan_event(event_db_path: str):
    event_db = Path(event_db_path)

    if not event_db.exists():
        raise RuntimeError(f"Event database not found: {event_db}")

    conn = sqlite3.connect(event_db, timeout=30)
    conn.row_factory = sqlite3.Row

    event = conn.execute(
        "SELECT * FROM event_info WHERE id = 1"
    ).fetchone()

    if not event:
        conn.close()
        raise RuntimeError("Event information is missing.")

    source_folder = Path(event["source_folder"])
    event_root = Path(event["event_root"])

    if not str(source_folder).startswith("/Volumes/"):
        conn.close()
        raise RuntimeError(
            "Safety stop: source folder is not on an external event drive."
        )

    if not source_folder.exists():
        conn.close()
        raise RuntimeError(
            f"Event drive or source folder is not connected: {source_folder}"
        )

    if not source_folder.is_dir():
        conn.close()
        raise RuntimeError(
            f"Source path is not a folder: {source_folder}"
        )

    detected = 0
    new_photos = 0
    changed_photos = 0
    unchanged = 0

    for path in source_folder.rglob("*"):
        if not path.is_file():
            continue

        if path.name.startswith("."):
            continue

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        try:
            path.relative_to(event_root)
            continue
        except ValueError:
            pass

        detected += 1

        try:
            stat = path.stat()
        except OSError:
            continue

        # Preserve the hard-drive folder structure for the customer gallery.
        relative_path = path.relative_to(source_folder)

        if str(relative_path.parent) == ".":
            relative_folder = ""
        else:
            relative_folder = str(relative_path.parent)

        existing = conn.execute(
            """
            SELECT id, file_size, modified_time
            FROM photos
            WHERE original_path = ?
            """,
            (str(path),),
        ).fetchone()

        # Existing unchanged photographs are already safe and need
        # no additional settling delay.
        if existing is not None:
            same_size = existing["file_size"] == stat.st_size
            same_mtime = existing["modified_time"] == stat.st_mtime

            if same_size and same_mtime:
                conn.execute(
                    """
                    UPDATE photos
                    SET relative_folder = ?
                    WHERE id = ?
                    """,
                    (
                        relative_folder,
                        existing["id"],
                    ),
                )
                unchanged += 1
                continue

        # New files and files that are actively changing must settle
        # before any preview job is created or reset.
        if not file_is_stable(path, stat):
            continue

        if not image_is_complete(path):
            continue

        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO photos (
                    original_path,
                    original_filename,
                    relative_folder,
                    file_size,
                    modified_time,
                    preview_status,
                    upload_status
                )
                VALUES (?, ?, ?, ?, ?, 'pending', 'pending')
                """,
                (
                    str(path),
                    path.name,
                    relative_folder,
                    stat.st_size,
                    stat.st_mtime,
                ),
            )

            photo_id = cursor.lastrowid

            conn.execute(
                """
                INSERT INTO processing_jobs (
                    photo_id,
                    job_type,
                    status
                )
                VALUES (?, 'create_preview', 'pending')
                """,
                (photo_id,),
            )

            new_photos += 1
            continue

        conn.execute(
            """
            UPDATE photos
            SET
                relative_folder = ?,
                file_size = ?,
                modified_time = ?,
                preview_status = 'pending',
                upload_status = 'pending',
                processed_at = NULL,
                uploaded_at = NULL,
                remote_preview_key = NULL,
                last_error = NULL
            WHERE id = ?
            """,
            (
                relative_folder,
                stat.st_size,
                stat.st_mtime,
                existing["id"],
            ),
        )

        pending_job = conn.execute(
            """
            SELECT id
            FROM processing_jobs
            WHERE photo_id = ?
              AND job_type = 'create_preview'
              AND status IN ('pending', 'processing')
            LIMIT 1
            """,
            (existing["id"],),
        ).fetchone()

        if not pending_job:
            conn.execute(
                """
                INSERT INTO processing_jobs (
                    photo_id,
                    job_type,
                    status
                )
                VALUES (?, 'create_preview', 'pending')
                """,
                (existing["id"],),
            )

        changed_photos += 1

    conn.commit()

    total_registered = conn.execute(
        "SELECT COUNT(*) FROM photos"
    ).fetchone()[0]

    pending_previews = conn.execute(
        """
        SELECT COUNT(*)
        FROM processing_jobs
        WHERE job_type = 'create_preview'
          AND status = 'pending'
        """
    ).fetchone()[0]

    conn.close()

    return {
        "detected_on_drive": detected,
        "new_photos": new_photos,
        "changed_photos": changed_photos,
        "unchanged": unchanged,
        "registered_total": total_registered,
        "previews_waiting": pending_previews,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.services.scanner /path/to/event.db")
        raise SystemExit(1)

    try:
        result = scan_event(sys.argv[1])
    except Exception as exc:
        print("")
        print("SCAN FAILED")
        print(exc)
        raise SystemExit(1)

    print("")
    print("===== STUPHIE PHOTO SCAN =====")
    print(f"Detected on drive : {result['detected_on_drive']}")
    print(f"New photographs   : {result['new_photos']}")
    print(f"Changed photos    : {result['changed_photos']}")
    print(f"Already known     : {result['unchanged']}")
    print(f"Registered total  : {result['registered_total']}")
    print(f"Previews waiting  : {result['previews_waiting']}")
    print("==============================")
