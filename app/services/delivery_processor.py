from __future__ import annotations

from pathlib import Path
import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image

from app.services.preview_processor import open_image_safely
from app.services.upload_processor import load_sync_config, sync_json


SOCIAL_MAX_EDGE = 2048
SOCIAL_JPEG_QUALITY = 90

A3_LONG_EDGE = 4961
A3_SHORT_EDGE = 3508
HIGH_RES_JPEG_QUALITY = 95


def _event_info(event_db_path: str):
    conn = sqlite3.connect(event_db_path)
    conn.row_factory = sqlite3.Row
    try:
        event = conn.execute(
            "SELECT * FROM event_info WHERE id = 1"
        ).fetchone()

        if not event:
            raise RuntimeError("Event information is missing.")

        return dict(event)
    finally:
        conn.close()


def _find_original(
    event_db_path: str,
    *,
    filename: str,
    folder_path: str,
) -> Path:
    conn = sqlite3.connect(event_db_path)
    conn.row_factory = sqlite3.Row

    try:
        clean_folder = str(folder_path or "").replace(
            "\\", "/"
        ).strip("/")

        if clean_folder:
            rows = conn.execute(
                """
                SELECT original_path
                FROM photos
                WHERE original_filename = ?
                  AND relative_folder = ?
                """,
                (
                    filename,
                    clean_folder,
                ),
            ).fetchall()
        else:
            # Compatibility for older orders created before folder_path
            # was preserved. Filename-only lookup is accepted only if
            # it resolves to exactly one source photograph.
            rows = conn.execute(
                """
                SELECT original_path
                FROM photos
                WHERE original_filename = ?
                """,
                (filename,),
            ).fetchall()

        if len(rows) != 1:
            raise RuntimeError(
                "Original photograph lookup was not unique: "
                f"{clean_folder}/{filename} matched {len(rows)} files."
            )

        path = Path(rows[0]["original_path"])

        if not path.is_file():
            raise RuntimeError(
                f"Original photograph is unavailable: {path}"
            )

        return path
    finally:
        conn.close()


def _safe_name(value: str) -> str:
    return (
        re.sub(r"[^A-Za-z0-9._-]+", "-", str(value))
        .strip("-")
        or "asset"
    )


def _make_derivative(
    original_path: Path,
    output_path: Path,
    delivery_type: str,
):
    image = open_image_safely(original_path)

    try:
        image = image.convert("RGB")

        if delivery_type == "low_res":
            image.thumbnail(
                (SOCIAL_MAX_EDGE, SOCIAL_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )

            image.save(
                output_path,
                "JPEG",
                quality=SOCIAL_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )

        elif delivery_type == "high_res":
            if image.width >= image.height:
                bounds = (
                    A3_LONG_EDGE,
                    A3_SHORT_EDGE,
                )
            else:
                bounds = (
                    A3_SHORT_EDGE,
                    A3_LONG_EDGE,
                )

            # thumbnail() never enlarges a smaller original.
            image.thumbnail(
                bounds,
                Image.Resampling.LANCZOS,
            )

            image.save(
                output_path,
                "JPEG",
                quality=HIGH_RES_JPEG_QUALITY,
                optimize=True,
                subsampling=0,
                dpi=(300, 300),
            )

        else:
            raise RuntimeError(
                f"Unsupported delivery type: {delivery_type}"
            )

    finally:
        image.close()


def _upload_delivery_asset(
    config: dict,
    *,
    gallery_ref: str,
    asset_ref: str,
    delivery_type: str,
    path: Path,
):
    boundary = "----StuphieDelivery" + uuid.uuid4().hex

    content = path.read_bytes()
    filename = path.name

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\n'
        "Content-Type: image/jpeg\r\n"
        "\r\n"
    ).encode("utf-8")

    body += content
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    base = config["STUPHIE_SYNC_BASE_URL"].rstrip("/")

    url = (
        base
        + "/api/sync/v1/galleries/"
        + urllib.parse.quote(gallery_ref, safe="")
        + "/delivery-assets/"
        + urllib.parse.quote(asset_ref, safe="")
    )

    request = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "X-Pirouette-Sync-Key":
                config["STUPHIE_SYNC_API_KEY"],
            "X-Pirouette-Delivery-Type":
                delivery_type,
            "Content-Type":
                f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=120,
        ) as response:
            raw = response.read()

    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(
            "utf-8",
            "replace",
        )

        raise RuntimeError(
            f"Delivery asset upload HTTP {exc.code}: "
            f"{detail[:500]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Delivery asset upload failed: {exc}"
        ) from exc

    result = json.loads(raw.decode("utf-8"))

    if (
        result.get("delivery_asset") != "ready"
        or result.get("delivery_type") != delivery_type
        or str(result.get("source_ref") or "")
        != asset_ref
    ):
        raise RuntimeError(
            "Cloud did not verify the private delivery asset."
        )

    return result


def _retry_cloud_order(
    config: dict,
    source_ref: str,
):
    return sync_json(
        config,
        "POST",
        (
            "/api/checkout/v1/sync/orders/"
            + urllib.parse.quote(source_ref, safe="")
            + "/retry"
        ),
    )


def process_next_delivery(event_db_path: str):
    event = _event_info(event_db_path)

    event_ref = str(
        event.get("public_slug") or ""
    ).strip()

    if not event_ref:
        raise RuntimeError(
            "Event public slug is missing."
        )

    config = load_sync_config()

    payload = sync_json(
        config,
        "GET",
        (
            "/api/checkout/v1/sync/inbound-orders"
            "?event_source_ref="
            + urllib.parse.quote(event_ref, safe="")
        ),
    )

    orders = payload.get("orders", [])

    if not isinstance(orders, list):
        raise RuntimeError(
            "Cloud inbound order response is invalid."
        )

    for order in orders:
        if not isinstance(order, dict):
            continue

        if str(order.get("payment_status") or "") != "paid":
            continue

        product_type = str(
            order.get("product_type") or ""
        )

        if product_type not in {"low_res", "high_res"}:
            continue

        status = str(order.get("status") or "")

        if status in {
            "completed",
            "cancelled",
            "refunded",
            "waiting_for_download",
        }:
            continue

        source_ref = str(
            order.get("source_ref") or ""
        ).strip()

        if not source_ref:
            continue

        items = order.get("items", [])

        if not isinstance(items, list) or not items:
            raise RuntimeError(
                f"Paid order {source_ref} contains no photographs."
            )

        cache_dir = (
            Path(event["event_root"])
            / "delivery-cache"
            / product_type
        )

        cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        generated = []

        try:
            for item in items:
                if not isinstance(item, dict):
                    continue

                asset_ref = str(
                    item.get("asset_source_ref") or ""
                ).strip()

                filename = str(
                    item.get("filename") or ""
                ).strip()

                folder_path = str(
                    item.get("folder_path") or ""
                ).replace("\\", "/").strip("/")

                if not asset_ref or not filename:
                    raise RuntimeError(
                        f"Order {source_ref} contains an "
                        "incomplete photograph reference."
                    )

                original_path = _find_original(
                    event_db_path,
                    filename=filename,
                    folder_path=folder_path,
                )

                output_path = (
                    cache_dir
                    / f"{_safe_name(asset_ref)}.jpg"
                )

                _make_derivative(
                    original_path,
                    output_path,
                    product_type,
                )

                generated.append(output_path)

                _upload_delivery_asset(
                    config,
                    gallery_ref=event_ref,
                    asset_ref=asset_ref,
                    delivery_type=product_type,
                    path=output_path,
                )

            result = _retry_cloud_order(
                config,
                source_ref,
            )

            final_status = str(
                result.get("status") or ""
            )

            if final_status not in {
                "waiting_for_download",
                "completed",
                "delivery_email_failed",
            }:
                raise RuntimeError(
                    "Cloud fulfilment did not complete after "
                    f"asset upload. Status: {final_status}"
                )

            return {
                "order_reference":
                    order.get("order_reference")
                    or source_ref,
                "product_type": product_type,
                "files": len(generated),
                "status": final_status,
            }

        finally:
            # Purchased derivatives are temporary on the Event Mac.
            # Originals remain untouched; persistent delivery copies
            # live privately in cloud storage.
            for path in generated:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    return None
