from pathlib import Path
import json
import sqlite3
import sys

from PIL import Image, ImageOps


BASE_DIR = Path(__file__).resolve().parent.parent.parent
BRANDS_PATH = BASE_DIR / "config" / "brand_profiles.json"


def load_brand(brand_id: str):
    with open(BRANDS_PATH, "r", encoding="utf-8") as f:
        brands = json.load(f)["brands"]

    for brand in brands:
        if brand["brand_id"] == brand_id:
            return brand

    raise RuntimeError(f"Brand profile not found: {brand_id}")


def open_image_safely(path: Path):
    image = Image.open(path)
    image = ImageOps.exif_transpose(image)

    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")

    return image


def apply_watermark(image: Image.Image, watermark_path: Path):
    base = image.convert("RGBA")

    source = Image.open(watermark_path).convert("RGBA")

    # Convert the supplied black DSI logo into a transparent white mark.
    grey = source.convert("L")
    mask = grey.point(lambda p: 255 - p)
    mask = mask.point(lambda p: 0 if p < 40 else p)

    mark = Image.new(
        "RGBA",
        source.size,
        (255, 255, 255, 0),
    )

    # Keep the watermark visible but allow the photograph to show through.
    opacity = 105
    mask = mask.point(lambda p: int(p * opacity / 255))
    mark.putalpha(mask)

    # Smaller marks - approximately 21% of the photograph width.
    target_width = max(90, int(base.width * 0.21))
    ratio = target_width / mark.width

    mark = mark.resize(
        (
            target_width,
            max(1, int(mark.height * ratio)),
        ),
        Image.Resampling.LANCZOS,
    )

    # Approved diagonal direction.
    mark = mark.rotate(
        30,
        expand=True,
        resample=Image.Resampling.BICUBIC,
    )

    # Lay marks out from the photograph dimensions rather than
    # using the rotated watermark dimensions for spacing.
    columns = 3
    rows = 4

    x_step = base.width / columns
    y_step = base.height / rows

    mark_w, mark_h = mark.size

    for row in range(rows):
        # Alternate rows are offset to avoid obvious straight columns.
        offset = x_step / 2 if row % 2 else 0

        for column in range(columns + 1):
            centre_x = (column * x_step) + offset
            centre_y = (row * y_step) + (y_step / 2)

            x = int(centre_x - mark_w / 2)
            y = int(centre_y - mark_h / 2)

            # Allow edge marks to be partially cropped naturally.
            base.alpha_composite(mark, (x, y))

    return base.convert("RGB")


def process_next_preview(event_db_path: str):
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
    brand_id = event["brand_id"]

    if not source_folder.exists():
        conn.close()
        raise RuntimeError(
            f"Event drive or source folder is not connected: {source_folder}"
        )

    brand = load_brand(brand_id)

    watermark_file = (brand.get("watermark_file") or "").strip()

    if not watermark_file:
        conn.close()
        raise RuntimeError(
            f"Safety stop: no watermark is configured for brand '{brand['display_name']}'."
        )

    watermark_path = Path(watermark_file).expanduser()

    if not watermark_path.is_absolute():
        watermark_path = BASE_DIR / watermark_path

    if not watermark_path.exists():
        conn.close()
        raise RuntimeError(
            f"Safety stop: watermark file cannot be found: {watermark_path}"
        )

    # Claim exactly one preview job atomically. BEGIN IMMEDIATE
    # prevents another worker selecting the same pending row.
    conn.execute("BEGIN IMMEDIATE")

    job = conn.execute(
        """
        SELECT
            j.id AS job_id,
            j.photo_id,
            p.original_path,
            p.original_filename
        FROM processing_jobs j
        JOIN photos p ON p.id = j.photo_id
        WHERE j.job_type = 'create_preview'
          AND j.status = 'pending'
        ORDER BY j.id
        LIMIT 1
        """
    ).fetchone()

    if not job:
        conn.rollback()
        conn.close()
        return None

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
            preview_status = 'processing',
            last_error = NULL
        WHERE id = ?
        """,
        (job["photo_id"],),
    )

    conn.commit()

    original_path = Path(job["original_path"])

    # A preview job may have been queued while an SD-card copy was
    # still in progress. Re-check the actual image here as a second
    # safety gate before doing any processing.
    try:
        with Image.open(original_path) as check_image:
            check_image.verify()
    except Exception:
        conn.execute(
            """
            UPDATE processing_jobs
            SET
                status = 'pending',
                started_at = NULL,
                last_error = NULL
            WHERE id = ?
            """,
            (job["job_id"],),
        )

        conn.execute(
            """
            UPDATE photos
            SET
                preview_status = 'pending',
                last_error = NULL
            WHERE id = ?
            """,
            (job["photo_id"],),
        )

        conn.commit()
        conn.close()
        return None

    preview_dir = event_root / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    preview_name = f"{job['photo_id']:08d}.jpg"
    preview_path = preview_dir / preview_name

    try:
        image = open_image_safely(original_path)

        image.thumbnail(
            (1800, 1800),
            Image.Resampling.LANCZOS,
        )

        image = apply_watermark(
            image,
            watermark_path,
        )

        image.save(
            preview_path,
            "JPEG",
            quality=82,
            optimize=True,
        )

        conn.execute(
            """
            UPDATE photos
            SET
                preview_path = ?,
                preview_status = 'ready',
                processed_at = CURRENT_TIMESTAMP,
                last_error = NULL
            WHERE id = ?
            """,
            (
                str(preview_path),
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

        upload_job = conn.execute(
            """
            SELECT id, status
            FROM processing_jobs
            WHERE photo_id = ?
              AND job_type = 'upload_preview'
            ORDER BY id DESC
            LIMIT 1
            """,
            (job["photo_id"],),
        ).fetchone()

        if upload_job:
            conn.execute(
                """
                UPDATE processing_jobs
                SET
                    status = 'pending',
                    attempts = 0,
                    started_at = NULL,
                    completed_at = NULL,
                    last_error = NULL
                WHERE id = ?
                """,
                (upload_job["id"],),
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
                (job["photo_id"],),
            )

        conn.commit()

        return {
            "photo_id": job["photo_id"],
            "original": str(original_path),
            "preview": str(preview_path),
        }

    except Exception as exc:
        conn.execute(
            """
            UPDATE photos
            SET
                preview_status = 'failed',
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
        print("Usage: python -m app.services.preview_processor /path/to/event.db")
        raise SystemExit(1)

    try:
        result = process_next_preview(sys.argv[1])
    except Exception as exc:
        print("")
        print("PREVIEW PROCESSING FAILED")
        print(exc)
        raise SystemExit(1)

    if result is None:
        print("")
        print("No preview jobs are waiting.")
        raise SystemExit(0)

    print("")
    print("===== PREVIEW CREATED =====")
    print(f"Photo ID : {result['photo_id']}")
    print(f"Original : {result['original']}")
    print(f"Preview  : {result['preview']}")
    print("===========================")
