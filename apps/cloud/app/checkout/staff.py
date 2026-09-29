from __future__ import annotations

from datetime import datetime, timezone

import secrets

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.checkout.fulfilment import process_paid_order
from app.checkout.audit import record_order_audit
from app.db.models import CloudOrder, Event


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
