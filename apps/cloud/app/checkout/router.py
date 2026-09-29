from __future__ import annotations

import json

import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.checkout.fulfilment import process_paid_order, status_payload
from app.checkout.service import checkout_options, paid_status
from app.db.models import CloudOrder, Event
from app.db.session import database_schema_is_ready


class CheckoutQuote(BaseModel):
    event_source_ref: str
    customer_at_event: bool = False
    product_type: str = Field(pattern=r"^(print|low_res|high_res|video)$")
    wants_home_delivery: bool = False


class CheckoutCreate(CheckoutQuote):
    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr
    customer_phone: str = Field(default="", max_length=80)
    payment_method: str = Field(pattern=r"^(service_desk|sumup)$")
    order_reference: str = Field(default="", max_length=120)
    source_ref: str | None = Field(default=None, max_length=200)
    asset_source_refs: list[str] = Field(default_factory=list)
    address_line_1: str = ""
    address_line_2: str = ""
    town: str = ""
    county: str = ""
    postcode: str = ""
    country: str = "United Kingdom"


class SyncedOrder(BaseModel):
    event_source_ref: str
    source_ref: str = Field(min_length=1, max_length=200)
    order_reference: str = Field(min_length=1, max_length=120)
    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr
    customer_phone: str = Field(default="", max_length=80)
    customer_at_event: bool = True
    product_type: str = Field(pattern=r"^(print|low_res|high_res|video)$")
    payment_method: str = Field(default="service_desk", max_length=30)
    fulfilment_method: str = Field(max_length=40)
    payment_status: str = Field(default="awaiting_payment", max_length=30)
    delivery_charge_pence: int = Field(default=0, ge=0, le=100000)
    asset_source_refs: list[str] = Field(default_factory=list)
    address_line_1: str = ""
    address_line_2: str = ""
    town: str = ""
    county: str = ""
    postcode: str = ""
    country: str = "United Kingdom"


def _ready(request: Request) -> None:
    if request.app.state.engine is None or not database_schema_is_ready(request.app.state.engine):
        raise HTTPException(503, "Database schema is not ready")


def _authorise_sync(request: Request, supplied: str | None) -> None:
    expected = request.app.state.settings.sync_api_key
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid sync key")


def _items_json(refs: list[str]) -> str:
    unique = list(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip()))
    return json.dumps([{"asset_source_ref": ref} for ref in unique])


def build_checkout_router() -> APIRouter:
    router = APIRouter(prefix="/api/checkout/v1", tags=["checkout"])

    @router.post("/quote")
    async def quote(payload: CheckoutQuote, request: Request):
        _ready(request)
        with request.app.state.session_factory() as session:
            event = session.scalar(select(Event).where(Event.source_ref == payload.event_source_ref))
            if event is None:
                raise HTTPException(404, "Event not found")
            return checkout_options(
                event,
                customer_at_event=payload.customer_at_event,
                product_type=payload.product_type,
                wants_home_delivery=payload.wants_home_delivery,
            )

    @router.post("/orders")
    async def create_order(payload: CheckoutCreate, request: Request):
        _ready(request)
        with request.app.state.session_factory() as session:
            event = session.scalar(select(Event).where(Event.source_ref == payload.event_source_ref))
            if event is None:
                raise HTTPException(404, "Event not found")
            if payload.source_ref:
                existing = session.scalar(select(CloudOrder).where(CloudOrder.source_ref == payload.source_ref))
                if existing is not None:
                    return status_payload(existing)
            options = checkout_options(
                event,
                customer_at_event=payload.customer_at_event,
                product_type=payload.product_type,
                wants_home_delivery=payload.wants_home_delivery,
            )
            if payload.payment_method not in options["payment_methods"]:
                raise HTTPException(400, "Payment method is not available")
            if options["address_required"] and not all(
                [payload.address_line_1.strip(), payload.town.strip(), payload.postcode.strip()]
            ):
                raise HTTPException(400, "A delivery address is required for this print order")
            order = CloudOrder(
                source_ref=payload.source_ref,
                event_id=event.id,
                order_reference=payload.order_reference.strip(),
                customer_name=payload.customer_name.strip(),
                customer_email=str(payload.customer_email).lower(),
                customer_phone=payload.customer_phone.strip(),
                customer_at_event=payload.customer_at_event,
                product_type=payload.product_type,
                payment_method=payload.payment_method,
                payment_status="awaiting_payment",
                fulfilment_method=str(options["fulfilment_method"]),
                order_items_json=_items_json(payload.asset_source_refs),
                address_line_1=payload.address_line_1.strip(),
                address_line_2=payload.address_line_2.strip(),
                town=payload.town.strip(),
                county=payload.county.strip(),
                postcode=payload.postcode.strip(),
                country=payload.country.strip() or "United Kingdom",
                delivery_charge_pence=int(options["delivery_charge_pence"]),
                status=str(options["initial_status"]),
            )
            session.add(order)
            session.commit()
            session.refresh(order)
            return status_payload(order) | {
                "id": order.id,
                "delivery_charge_pence": order.delivery_charge_pence,
                "delivery_charge_display": f"£{order.delivery_charge_pence / 100:.2f}",
            }

    @router.post("/orders/{order_id}/mark-paid")
    async def mark_paid(
        order_id: str, request: Request, x_pirouette_sync_key: str | None = Header(default=None)
    ):
        _authorise_sync(request, x_pirouette_sync_key)
        _ready(request)
        with request.app.state.session_factory() as session:
            order = session.get(CloudOrder, order_id)
            if order is None:
                raise HTTPException(404, "Order not found")
            if order.payment_method != "service_desk":
                raise HTTPException(400, "Online SumUp payments must be verified by SumUp")
            order.payment_status = "paid"
            order.status = paid_status(order.product_type, order.fulfilment_method)
            session.commit()
            order = process_paid_order(session, request.app.state.settings, order)
            return status_payload(order) | {"id": order.id}

    @router.put("/sync/orders/{source_ref}")
    async def sync_order(
        source_ref: str,
        payload: SyncedOrder,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _ready(request)
        _authorise_sync(request, x_pirouette_sync_key)
        if source_ref != payload.source_ref:
            raise HTTPException(400, "Source reference mismatch")
        with request.app.state.session_factory() as session:
            event = session.scalar(select(Event).where(Event.source_ref == payload.event_source_ref))
            if event is None:
                raise HTTPException(404, "Event not found")
            order = session.scalar(select(CloudOrder).where(CloudOrder.source_ref == source_ref))
            if order is None:
                order = CloudOrder(source_ref=source_ref, event_id=event.id)
                session.add(order)
            order.event_id = event.id
            order.order_reference = payload.order_reference.strip()
            order.customer_name = payload.customer_name.strip()
            order.customer_email = str(payload.customer_email).lower()
            order.customer_phone = payload.customer_phone.strip()
            order.customer_at_event = payload.customer_at_event
            order.product_type = payload.product_type
            order.payment_method = payload.payment_method
            order.fulfilment_method = payload.fulfilment_method
            order.payment_status = payload.payment_status
            order.delivery_charge_pence = payload.delivery_charge_pence
            order.order_items_json = _items_json(payload.asset_source_refs)
            order.address_line_1 = payload.address_line_1.strip()
            order.address_line_2 = payload.address_line_2.strip()
            order.town = payload.town.strip()
            order.county = payload.county.strip()
            order.postcode = payload.postcode.strip()
            order.country = payload.country.strip() or "United Kingdom"
            order.last_synced_at = datetime.now(timezone.utc)
            if order.status in {"", "awaiting_payment"}:
                order.status = "awaiting_payment"
            session.commit(); session.refresh(order)
            if payload.payment_status == "paid":
                order = process_paid_order(session, request.app.state.settings, order)
            return status_payload(order) | {"id": order.id}

    @router.get("/sync/inbound-orders")
    async def inbound_orders(
        event_source_ref: str, request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _ready(request)
        _authorise_sync(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            event = session.scalar(select(Event).where(Event.source_ref == event_source_ref))
            if event is None:
                raise HTTPException(404, "Event not found")
            # Only expose committed customer orders to the Offline event system.
            # A SumUp checkout is merely a draft until payment is confirmed; importing
            # awaiting-payment rows caused duplicate Live Orders when a customer backed
            # out of online payment and chose Pay at the Stand instead.
            orders = list(session.scalars(select(CloudOrder).where(
                CloudOrder.event_id == event.id,
                CloudOrder.source_ref.like("cloud:%"),
                (CloudOrder.payment_status == "paid") | (CloudOrder.payment_method == "service_desk"),
            ).order_by(CloudOrder.created_at.asc())))
            payload=[]
            for order in orders:
                try: items=json.loads(order.order_items_json or "[]")
                except Exception: items=[]
                payload.append({
                    "source_ref": order.source_ref, "order_reference": order.order_reference,
                    "customer_name": order.customer_name, "customer_email": order.customer_email,
                    "customer_phone": order.customer_phone, "product_type": order.product_type,
                    "payment_method": order.payment_method, "payment_status": order.payment_status,
                    "payment_amount_pence": order.payment_amount_pence, "fulfilment_method": order.fulfilment_method,
                    "sumup_checkout_id": order.sumup_checkout_id,
                    "sumup_checkout_reference": order.sumup_checkout_reference,
                    "sumup_status": order.sumup_status,
                    "sumup_transaction_code": order.sumup_transaction_code,
                    "payment_verified_at": order.payment_verified_at.isoformat() if order.payment_verified_at else None,
                    "status": order.status, "delivery_charge_pence": order.delivery_charge_pence,
                    "created_at": order.created_at.isoformat() if order.created_at else None,
                    "completed_at": order.completed_at.isoformat() if order.completed_at else None,
                    "items": items,
                })
            return {"orders": payload}

    @router.get("/sync/orders/{source_ref}")
    async def sync_order_status(
        source_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _ready(request)
        _authorise_sync(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            order = session.scalar(select(CloudOrder).where(CloudOrder.source_ref == source_ref))
            if order is None:
                raise HTTPException(404, "Order not found")
            return status_payload(order)

    @router.post("/sync/orders/{source_ref}/retry")
    async def retry_order(
        source_ref: str,
        request: Request,
        x_pirouette_sync_key: str | None = Header(default=None),
    ):
        _ready(request)
        _authorise_sync(request, x_pirouette_sync_key)
        with request.app.state.session_factory() as session:
            order = session.scalar(select(CloudOrder).where(CloudOrder.source_ref == source_ref))
            if order is None:
                raise HTTPException(404, "Order not found")
            if order.payment_status != "paid":
                raise HTTPException(400, "Order is not paid")
            order = process_paid_order(session, request.app.state.settings, order)
            return status_payload(order)

    return router
