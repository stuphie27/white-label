from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import secrets
from fastapi import APIRouter, BackgroundTasks, Form, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db.models import CustomerDelivery
from app.downloads.service import complete_download, expire_due, find_by_token, revoke_delivery, send_due_reminders
from app.storage import download_to_temp, exists


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

    @router.get("/delivery/{token}", response_class=HTMLResponse, include_in_schema=False)
    async def delivery_page(request: Request, token: str):
        with request.app.state.session_factory() as session:
            expire_due(session, request.app.state.settings)
            d = find_by_token(session, token)
        if not d:
            return templates.TemplateResponse(request=request, name="delivery_closed.html", context={"message":"This delivery link is invalid or has been removed."}, status_code=404)
        if d.status in {"closed", "expired"} or d.deleted_at:
            return templates.TemplateResponse(request=request, name="delivery_closed.html", context={"message":"This secure delivery has expired."})
        return templates.TemplateResponse(request=request, name="delivery.html", context={"delivery":d, "token":token})

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
            if not exists(settings, d.zip_path):
                return templates.TemplateResponse(
                    request=request,
                    name="delivery_closed.html",
                    context={
                        "message": (
                            "We’re sorry, your secure download needs to be prepared again. "
                            "Please contact Sophie’s Photography and we will restore your delivery."
                        )
                    },
                    status_code=410,
                )

            path = download_to_temp(settings, d.zip_path, suffix=".zip")
            temporary_copy = str(d.zip_path).startswith("spaces://")
            delivery_id = d.id
            filename = f"Sophies-Photography-{d.order_reference or 'Delivery'}.zip"

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
