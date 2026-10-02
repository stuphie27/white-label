from __future__ import annotations

from datetime import datetime, timedelta, timezone

import hashlib
import smtplib
from email.message import EmailMessage
import secrets

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.checkout.fulfilment import process_paid_order
from app.operations import record_order_audit
from app.db.models import CloudOrder, Event, StaffOrdersLoginToken


def build_checkout_staff_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/staff", tags=["orders"])

    def _authenticated(request: Request) -> bool:
        return bool(request.session.get("staff_authenticated"))

    def _staff_order_csrf(request: Request) -> str:
        token = secrets.token_urlsafe(32)
        request.session["staff_order_csrf"] = token
        return token

    def _valid_staff_order_csrf(
        request: Request,
        supplied: str,
    ) -> bool:
        expected = str(
            request.session.get("staff_order_csrf", "")
        )
        return bool(
            expected
            and supplied
            and secrets.compare_digest(expected, supplied)
        )

    ORDERS_ACCESS_EMAIL = "photos@sophiesphotography.co.uk"
    ORDERS_ACCESS_TTL_MINUTES = 20

    def _orders_token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _send_orders_magic_email(
        request: Request,
        *,
        token: str,
    ) -> None:
        settings = request.app.state.settings

        if not settings.smtp_host:
            raise RuntimeError("SMTP is not configured for Orders access.")

        base_url = str(settings.public_base_url or "").rstrip("/")
        secure_link = f"{base_url}/staff/orders/access/{token}"

        message = EmailMessage()
        message["From"] = (
            f"{settings.smtp_from_name} "
            f"<{settings.smtp_from_email}>"
        )
        message["To"] = ORDERS_ACCESS_EMAIL
        message["Subject"] = "Your Stuphie Online Orders access link"

        message.set_content(
            "Hello,\n\n"
            "Use this private link to access the Stuphie Online Orders dashboard:\n\n"
            f"{secure_link}\n\n"
            "This link expires in 20 minutes and can only be used once.\n\n"
            "If you did not request this link, you can ignore this email."
        )

        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=20,
        ) as smtp:
            smtp.ehlo()

            if settings.smtp_use_tls:
                smtp.starttls()
                smtp.ehlo()

            if settings.smtp_username:
                smtp.login(
                    settings.smtp_username,
                    settings.smtp_password,
                )

            smtp.send_message(message)

    @router.get(
        "/orders/request-access",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def request_orders_access(request: Request):
        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if factory is None:
            raise HTTPException(503, "Database unavailable.")

        now = datetime.now(timezone.utc)
        raw_token = secrets.token_urlsafe(48)

        with factory() as session:
            session.query(StaffOrdersLoginToken).filter(
                StaffOrdersLoginToken.staff_email
                == ORDERS_ACCESS_EMAIL,
                StaffOrdersLoginToken.used_at.is_(None),
            ).update(
                {"used_at": now},
                synchronize_session=False,
            )

            session.add(
                StaffOrdersLoginToken(
                    staff_email=ORDERS_ACCESS_EMAIL,
                    token_hash=_orders_token_hash(raw_token),
                    expires_at=(
                        now
                        + timedelta(
                            minutes=ORDERS_ACCESS_TTL_MINUTES
                        )
                    ),
                )
            )
            session.commit()

        try:
            _send_orders_magic_email(
                request,
                token=raw_token,
            )
        except Exception:
            with factory() as session:
                session.query(StaffOrdersLoginToken).filter(
                    StaffOrdersLoginToken.token_hash
                    == _orders_token_hash(raw_token)
                ).update(
                    {"used_at": now},
                    synchronize_session=False,
                )
                session.commit()

            raise HTTPException(
                503,
                "Orders access email could not be sent.",
            )

        return HTMLResponse(
            "<h1>Orders access link sent</h1>"
            "<p>Check photos@sophiesphotography.co.uk for the "
            "secure 20-minute access link.</p>"
        )

    @router.get(
        "/orders/access/{token}",
        include_in_schema=False,
    )
    async def orders_magic_login(
        token: str,
        request: Request,
    ):
        factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if factory is None:
            raise HTTPException(503, "Database unavailable.")

        now = datetime.now(timezone.utc)

        with factory() as session:
            row = session.scalar(
                select(StaffOrdersLoginToken).where(
                    StaffOrdersLoginToken.token_hash
                    == _orders_token_hash(token)
                )
            )

            if (
                row is None
                or row.used_at is not None
                or row.staff_email != ORDERS_ACCESS_EMAIL
                or row.expires_at <= now
            ):
                raise HTTPException(
                    403,
                    "This Orders access link is invalid or expired.",
                )

            row.used_at = now
            session.commit()

        request.session["staff_authenticated"] = True
        request.session["staff_email"] = ORDERS_ACCESS_EMAIL
        request.session["staff_role"] = "super_admin"
        request.session["staff_last_activity"] = int(now.timestamp())

        return RedirectResponse(
            "/staff/orders",
            status_code=303,
        )


    @router.get("/orders", response_class=HTMLResponse, include_in_schema=False)
    async def orders(request: Request, queue: str = ""):
        if not _authenticated(request):
            return RedirectResponse("/staff/login", status_code=status.HTTP_303_SEE_OTHER)
        rows = []
        if request.app.state.session_factory is not None:
            with request.app.state.session_factory() as session:
                statement = select(CloudOrder, Event).join(Event, CloudOrder.event_id == Event.id)
                if queue == "awaiting_payment":
                    statement = statement.where(CloudOrder.payment_status == "awaiting_payment")
                elif queue == "print":
                    statement = statement.where(CloudOrder.status.in_(["awaiting_print_production", "awaiting_print_production_for_dispatch", "printing"]))
                elif queue == "dispatch":
                    statement = statement.where(CloudOrder.status.in_(["awaiting_dispatch", "dispatched"]))
                elif queue == "digital":
                    statement = statement.where(CloudOrder.status.in_(["ready_for_automatic_delivery", "waiting_for_download", "delivery_email_failed"]))
                elif queue == "high_res":
                    statement = statement.where(CloudOrder.status == "awaiting_high_resolution_preparation")
                elif queue == "failed":
                    statement = statement.where(CloudOrder.status.in_(["payment_failed", "delivery_failed", "delivery_email_failed"]))
                rows = list(session.execute(statement.order_by(CloudOrder.created_at.desc())).all())
        return templates.TemplateResponse(
            request=request,
            name="cloud_orders.html",
            context={
                "orders": rows,
                "staff_email": request.session.get("staff_email", ""),
                "active_nav": "orders",
                "version": request.app.state.settings.version,
                "queue": queue,
                "csrf_token": _staff_order_csrf(request),
            },
        )


    @router.post(
        "/orders/{order_id}/action",
        include_in_schema=False,
    )
    async def order_action(
        order_id: str,
        request: Request,
        action: str = Form(...),
        csrf_token: str = Form(...),
    ):
        if not _authenticated(request):
            return RedirectResponse(
                "/staff/login",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        if not _valid_staff_order_csrf(
            request,
            csrf_token,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid staff security request.",
            )

        with request.app.state.session_factory() as session:
            order = session.get(
                CloudOrder,
                order_id,
            )

            if order is None:
                raise HTTPException(
                    404,
                    "Order not found",
                )

            if order.payment_status != "paid":
                raise HTTPException(
                    400,
                    "Only paid orders can be fulfilled",
                )

            current = str(order.status or "")
            old_status = current

            if action == "start_printing":
                if (
                    order.product_type != "print"
                    or current not in {
                        "awaiting_print_production",
                        "awaiting_print_production_for_dispatch",
                    }
                ):
                    raise HTTPException(
                        400,
                        "Order is not ready for printing",
                    )

                order.status = "printing"

            elif action == "ready_for_collection":
                if (
                    order.product_type != "print"
                    or order.fulfilment_method
                    != "event_collection"
                    or current != "printing"
                ):
                    raise HTTPException(
                        400,
                        "Order cannot be moved to collection",
                    )

                order.status = "ready_for_collection"

            elif action == "collected":
                if (
                    order.product_type != "print"
                    or order.fulfilment_method
                    != "event_collection"
                    or current != "ready_for_collection"
                ):
                    raise HTTPException(
                        400,
                        "Order is not ready for collection",
                    )

                order.status = "completed"
                order.completed_at = datetime.now(
                    timezone.utc
                )

            elif action == "ready_for_dispatch":
                if (
                    order.product_type != "print"
                    or order.fulfilment_method
                    != "home_delivery"
                    or current != "printing"
                ):
                    raise HTTPException(
                        400,
                        "Order cannot be moved to dispatch",
                    )

                order.status = "awaiting_dispatch"

            elif action == "dispatched":
                if (
                    order.product_type != "print"
                    or order.fulfilment_method
                    != "home_delivery"
                    or current != "awaiting_dispatch"
                ):
                    raise HTTPException(
                        400,
                        "Order is not ready for dispatch",
                    )

                order.status = "dispatched"
                order.completed_at = (
                    order.completed_at
                    or datetime.now(timezone.utc)
                )

            elif action == "prepare_high_res":
                if (
                    order.product_type != "high_res"
                    or current
                    != "awaiting_high_resolution_preparation"
                ):
                    raise HTTPException(
                        400,
                        "Order is not awaiting High Res preparation",
                    )

                # This explicit staff action opens the normal
                # secure-delivery path. If the prepared originals
                # have not arrived yet the order safely moves to
                # waiting_for_cloud_assets.
                order.status = "waiting_for_cloud_assets"
                session.commit()

                process_paid_order(
                    session,
                    request.app.state.settings,
                    order,
                )

                return RedirectResponse(
                    "/staff/orders?queue=high_res",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            else:
                raise HTTPException(
                    400,
                    "Unknown fulfilment action",
                )

            order.fulfilment_error = ""

            record_order_audit(
                session,
                order,
                "staff_fulfilment_action",
                old_status=old_status,
                new_status=order.status,
                detail=action,
            )

            session.commit()

        return RedirectResponse(
            "/staff/orders",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @router.post("/orders/{order_id}/retry", include_in_schema=False)
    async def retry_order(
        order_id: str,
        request: Request,
        csrf_token: str = Form(...),
    ):
        if not _authenticated(request):
            return RedirectResponse("/staff/login", status_code=status.HTTP_303_SEE_OTHER)

        if not _valid_staff_order_csrf(request, csrf_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid staff security request.",
            )
        with request.app.state.session_factory() as session:
            order = session.get(CloudOrder, order_id)
            if order is None:
                raise HTTPException(404, "Order not found")
            if order.payment_status != "paid":
                raise HTTPException(400, "Only paid orders can be retried")
            process_paid_order(session, request.app.state.settings, order)
        return RedirectResponse("/staff/orders", status_code=status.HTTP_303_SEE_OTHER)

    return router
