from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import secrets
from fastapi import APIRouter, BackgroundTasks, Form, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db.models import (
    CustomerDelivery,
    CloudOrder,
    Event,
    Gallery,
    GalleryAsset,
)
from app.branding import get_event_brand
from app.downloads.service import complete_download, expire_due, find_by_token, revoke_delivery, send_due_reminders
from app.storage import (
    download_to_temp,
    exists,
    presigned_get_url,
)
from app.storage import client as spaces_client


def _staff_delivery_csrf(request: Request) -> str:
    token = secrets.token_urlsafe(24)
    request.session["staff_delivery_csrf"] = token
    return token


def _valid_staff_delivery_csrf(
    request: Request,
    supplied: str,
) -> bool:
    expected = str(
        request.session.get(
            "staff_delivery_csrf",
            "",
        )
    )
    return bool(
        expected
        and supplied
        and secrets.compare_digest(
            expected,
            supplied,
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_downloads_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(tags=["downloads"])

    @router.get(
        "/delivery/{token}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def delivery_page(
        request: Request,
        token: str,
    ):
        with request.app.state.session_factory() as session:
            expire_due(
                session,
                request.app.state.settings,
            )

            d = find_by_token(
                session,
                token,
            )

            if not d:
                return templates.TemplateResponse(
                    request=request,
                    name="delivery_closed.html",
                    context={
                        "message": (
                            "This delivery link is invalid "
                            "or has been removed."
                        )
                    },
                    status_code=404,
                )

            if (
                d.status in {"closed", "expired"}
                or d.deleted_at
            ):
                return templates.TemplateResponse(
                    request=request,
                    name="delivery_closed.html",
                    context={
                        "message": (
                            "This secure delivery has expired."
                        )
                    },
                )

            order = None
            event = None

            if d.source_ref:
                order = session.scalar(
                    select(CloudOrder).where(
                        CloudOrder.source_ref
                        == d.source_ref
                    )
                )

            if order is not None:
                event = session.get(
                    Event,
                    order.event_id,
                )

            brand = get_event_brand(event)

            support_email = str(
                brand.get("sender_email")
                or request.app.state.settings.smtp_from_email
                or ""
            ).strip()

            return templates.TemplateResponse(
                request=request,
                name="delivery.html",
                context={
                    "delivery": d,
                    "token": token,
                    "brand": brand,
                    "support_email": support_email,
                },
            )

    def _mobile_delivery_items(
        session,
        delivery: CustomerDelivery,
    ) -> tuple[CloudOrder | None, list[dict]]:
        if not delivery.source_ref:
            return None, []

        order = session.scalar(
            select(CloudOrder).where(
                CloudOrder.source_ref
                == delivery.source_ref
            )
        )

        if order is None:
            return None, []

        try:
            raw_items = json.loads(
                order.order_items_json or "[]"
            )
        except Exception:
            raw_items = []

        if not isinstance(raw_items, list):
            raw_items = []

        resolved = []

        for item in raw_items:
            if not isinstance(item, dict):
                continue

            asset_ref = str(
                item.get("asset_source_ref") or ""
            ).strip()

            if not asset_ref:
                continue

            asset = session.scalar(
                select(GalleryAsset)
                .join(
                    Gallery,
                    GalleryAsset.gallery_id
                    == Gallery.id,
                )
                .where(
                    Gallery.event_id
                    == order.event_id,
                    GalleryAsset.source_ref
                    == asset_ref,
                )
            )

            if asset is None:
                continue

            storage_path = (
                asset.highres_delivery_storage_path
                if order.product_type == "high_res"
                else asset.delivery_storage_path
            )

            storage_path = str(
                storage_path or ""
            ).strip()

            if not storage_path:
                continue

            customer_filename = str(
                item.get("filename")
                or asset.filename
                or "photograph.jpg"
            ).strip()

            filename_path = Path(
                customer_filename
            )

            if (
                filename_path.suffix.lower()
                not in {".jpg", ".jpeg"}
            ):
                customer_filename = (
                    filename_path.stem + ".jpg"
                )

            safe_filename = "".join(
                c
                if c.isalnum()
                or c in " ._-()"
                else "-"
                for c in customer_filename
            ).strip() or "photograph.jpg"

            resolved.append(
                {
                    "filename": safe_filename,
                    "storage_path": storage_path,
                }
            )

        return order, resolved


    @router.get(
        "/delivery/{token}/photos",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def delivery_photos_page(
        request: Request,
        token: str,
    ):
        with request.app.state.session_factory() as session:
            d = find_by_token(
                session,
                token,
            )

            if (
                not d
                or d.deleted_at
                or d.status in {"closed", "expired"}
                or _as_utc(d.expires_at)
                <= datetime.now(timezone.utc)
            ):
                return templates.TemplateResponse(
                    request=request,
                    name="delivery_closed.html",
                    context={
                        "message": (
                            "This secure delivery has expired."
                        )
                    },
                    status_code=410,
                )

            order, items = _mobile_delivery_items(
                session,
                d,
            )

            event = None

            if order is not None:
                event = session.get(
                    Event,
                    order.event_id,
                )

            brand = get_event_brand(event)

            support_email = str(
                brand.get("sender_email")
                or request.app.state.settings.smtp_from_email
                or ""
            ).strip()

            visible_items = []

            preview_client = None

            for index, item in enumerate(items):
                asset_ref = None

                try:
                    raw_items = json.loads(
                        order.order_items_json or "[]"
                    )
                except Exception:
                    raw_items = []

                if (
                    isinstance(raw_items, list)
                    and index < len(raw_items)
                    and isinstance(raw_items[index], dict)
                ):
                    asset_ref = str(
                        raw_items[index].get(
                            "asset_source_ref"
                        )
                        or ""
                    ).strip()

                preview_url = ""

                if asset_ref and order is not None:
                    asset = session.scalar(
                        select(GalleryAsset)
                        .join(
                            Gallery,
                            GalleryAsset.gallery_id
                            == Gallery.id,
                        )
                        .where(
                            Gallery.event_id
                            == order.event_id,
                            GalleryAsset.source_ref
                            == asset_ref,
                        )
                    )

                    if asset is not None:
                        preview_storage = str(
                            asset.storage_path or ""
                        ).strip()

                        if preview_storage.startswith(
                            "spaces://"
                        ):
                            if preview_client is None:
                                preview_client = spaces_client(
                                    request.app.state.settings
                                )

                            preview_url = presigned_get_url(
                                request.app.state.settings,
                                preview_storage,
                                expires_seconds=900,
                                storage_client=preview_client,
                            )
                        elif preview_storage:
                            preview_url = (
                                f"/g/{asset.gallery_id}/assets/"
                                f"{asset.id}"
                            )

                visible_items.append(
                    {
                        "index": index,
                        "filename": item["filename"],
                        "url": (
                            f"/delivery/{token}/photo/{index}"
                        ),
                        "preview_url": preview_url,
                    }
                )

            return templates.TemplateResponse(
                request=request,
                name="delivery_photos.html",
                context={
                    "delivery": d,
                    "token": token,
                    "brand": brand,
                    "support_email": support_email,
                    "items": visible_items,
                },
            )


    @router.get(
        "/delivery/{token}/items",
        include_in_schema=False,
    )
    async def delivery_items(
        request: Request,
        token: str,
    ):
        with request.app.state.session_factory() as session:
            d = find_by_token(
                session,
                token,
            )

            if (
                not d
                or d.deleted_at
                or d.status in {"closed", "expired"}
                or _as_utc(d.expires_at)
                <= datetime.now(timezone.utc)
            ):
                raise HTTPException(
                    410,
                    "This delivery has expired or is closed",
                )

            _order, items = _mobile_delivery_items(
                session,
                d,
            )

            return {
                "delivery_type": d.delivery_type,
                "item_count": len(items),
                "items": [
                    {
                        "index": index,
                        "filename": item["filename"],
                        "url": (
                            f"/delivery/{token}/photo/{index}"
                        ),
                    }
                    for index, item
                    in enumerate(items)
                ],
            }


    @router.get(
        "/delivery/{token}/photo/{item_index}",
        include_in_schema=False,
    )
    async def delivery_photo(
        request: Request,
        token: str,
        item_index: int,
        background_tasks: BackgroundTasks,
    ):
        with request.app.state.session_factory() as session:
            d = find_by_token(
                session,
                token,
            )

            if (
                not d
                or d.deleted_at
                or d.status in {"closed", "expired"}
                or _as_utc(d.expires_at)
                <= datetime.now(timezone.utc)
            ):
                raise HTTPException(
                    410,
                    "This delivery has expired or is closed",
                )

            _order, items = _mobile_delivery_items(
                session,
                d,
            )

            if (
                item_index < 0
                or item_index >= len(items)
            ):
                raise HTTPException(
                    404,
                    "Purchased photograph not found",
                )

            item = items[item_index]
            settings = request.app.state.settings
            storage_path = item["storage_path"]

            if not exists(
                settings,
                storage_path,
            ):
                raise HTTPException(
                    410,
                    "This purchased photograph "
                    "needs to be prepared again",
                )

            path = download_to_temp(
                settings,
                storage_path,
                suffix=".jpg",
            )

            temporary_copy = storage_path.startswith(
                "spaces://"
            )

            delivery_id = d.id
            filename = item["filename"]

        def finish():
            try:
                with request.app.state.session_factory() as session:
                    complete_download(
                        session,
                        request.app.state.settings,
                        delivery_id,
                    )
            finally:
                if temporary_copy:
                    path.unlink(
                        missing_ok=True
                    )

        background_tasks.add_task(
            finish
        )

        return FileResponse(
            path,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    'inline; filename="'
                    + filename.replace('"', "")
                    + '"'
                ),
            },
            background=background_tasks,
        )


    @router.get("/delivery/{token}/download", include_in_schema=False)
    async def delivery_download(request: Request, token: str, background_tasks: BackgroundTasks):
        with request.app.state.session_factory() as session:
            d = find_by_token(session, token)
            if (
                not d
                or d.deleted_at
                or d.status in {"closed", "expired"}
                or _as_utc(d.expires_at) <= datetime.now(timezone.utc)
            ):
                raise HTTPException(410, "This delivery has expired or is closed")
            settings = request.app.state.settings

            order = None
            event = None

            if d.source_ref:
                order = session.scalar(
                    select(CloudOrder).where(
                        CloudOrder.source_ref == d.source_ref
                    )
                )

            if order is not None:
                event = session.get(
                    Event,
                    order.event_id,
                )

            brand = get_event_brand(event)

            display_name = str(
                brand.get("display_name")
                or "Photography"
            ).strip()

            if not exists(settings, d.zip_path):
                return templates.TemplateResponse(
                    request=request,
                    name="delivery_closed.html",
                    context={
                        "message": (
                            "We’re sorry, your secure download needs to be prepared again. "
                            f"Please contact {display_name} and we will restore your delivery."
                        )
                    },
                    status_code=410,
                )

            path = download_to_temp(settings, d.zip_path, suffix=".zip")
            temporary_copy = str(d.zip_path).startswith("spaces://")
            delivery_id = d.id
            safe_brand = "".join(
                c if c.isalnum() or c in "-_" else "-"
                for c in display_name
            ).strip("-") or "Photography"

            filename = (
                f"{safe_brand}-"
                f"{d.order_reference or 'Delivery'}.zip"
            )

        def finish():
            try:
                with request.app.state.session_factory() as session:
                    complete_download(
                        session,
                        request.app.state.settings,
                        delivery_id,
                    )
            finally:
                if temporary_copy:
                    path.unlink(missing_ok=True)

        background_tasks.add_task(finish)

        return FileResponse(
            path,
            media_type="application/zip",
            filename=filename,
            background=background_tasks,
        )

    @router.get("/staff/deliveries", response_class=HTMLResponse, include_in_schema=False)
    async def staff_deliveries(request: Request):
        if not request.session.get("staff_authenticated"):
            return RedirectResponse("/staff/login", status_code=303)
        with request.app.state.session_factory() as session:
            send_due_reminders(session, request.app.state.settings)
            expire_due(session, request.app.state.settings)
            deliveries=list(session.scalars(select(CustomerDelivery).order_by(CustomerDelivery.created_at.desc())))
        return templates.TemplateResponse(
            request=request,
            name="deliveries_list.html",
            context={
                "deliveries": deliveries,
                "staff_email": request.session.get(
                    "staff_email",
                    "",
                ),
                "csrf_token": _staff_delivery_csrf(
                    request
                ),
            },
        )


    @router.post(
        "/staff/deliveries/{delivery_id}/revoke",
        include_in_schema=False,
    )
    async def staff_revoke_delivery(
        request: Request,
        delivery_id: str,
        csrf_token: str = Form(...),
    ):
        if not request.session.get("staff_authenticated"):
            return RedirectResponse(
                "/staff/login",
                status_code=303,
            )

        if not _valid_staff_delivery_csrf(
            request,
            csrf_token,
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Invalid or expired form token",
            )

        with request.app.state.session_factory() as session:
            found = revoke_delivery(
                session,
                request.app.state.settings,
                delivery_id,
            )

        if not found:
            raise HTTPException(
                404,
                "Secure delivery not found",
            )

        return RedirectResponse(
            "/staff/deliveries?revoked=1",
            status_code=303,
        )


    return router
