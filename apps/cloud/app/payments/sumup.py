from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checkout.fulfilment import process_paid_order
from app.db.models import CloudOrder
from app.operations import record_order_audit

LOGGER = logging.getLogger("pirouette.sumup")


class SumUpError(RuntimeError):
    pass


@dataclass(frozen=True)
class HostedCheckout:
    checkout_id: str
    checkout_url: str
    status: str


def _request(settings, method: str, path: str, payload: dict | None = None) -> dict:
    if not settings.sumup_api_key:
        raise SumUpError("SumUp API key is not configured")
    url = settings.sumup_api_base.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {settings.sumup_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"Pirouette-Cloud/{settings.version}",
        },
    )
    try:
        with urlopen(request, timeout=settings.sumup_timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise SumUpError(f"SumUp returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise SumUpError(f"SumUp could not be reached: {exc}") from exc
    try:
        value = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise SumUpError("SumUp returned an invalid JSON response") from exc
    if not isinstance(value, dict):
        raise SumUpError("SumUp returned an unexpected response")
    return value


def customer_safe_error(exc: Exception) -> str:
    """Return a customer-safe, actionable explanation for hosted checkout failures."""
    text = str(exc or "").strip()
    lower = text.lower()
    if "api key is not configured" in lower or "merchant code is not configured" in lower:
        return "Secure online payment is temporarily unavailable because SumUp is not fully configured on the online gallery. Please choose Pay at the Stand or ask a member of staff."
    if "http 401" in lower:
        return "SumUp could not authorise the online payment connection. Please choose Pay at the Stand while we reconnect secure online payments."
    if "http 403" in lower:
        return "The SumUp connection does not currently have permission to create online checkouts. Please choose Pay at the Stand while we correct the payment permission."
    if "http 400" in lower:
        return "SumUp could not create this secure checkout. Please check the order details or choose Pay at the Stand."
    if "could not be reached" in lower:
        return "SumUp is temporarily unreachable. Your basket is safe — please try again or choose Pay at the Stand."
    return "We could not open the secure SumUp payment page. Your basket is safe — please try again or choose Pay at the Stand."


def create_hosted_checkout(settings, *, reference: str, amount_pence: int, description: str, redirect_url: str, return_url: str) -> HostedCheckout:
    if amount_pence <= 0:
        raise SumUpError("Checkout amount must be greater than zero")
    if not settings.sumup_merchant_code:
        raise SumUpError("SumUp merchant code is not configured")
    payload = {
        "amount": float(Decimal(amount_pence) / Decimal(100)),
        "checkout_reference": reference[:90],
        "currency": settings.sumup_currency,
        "description": description[:200],
        "merchant_code": settings.sumup_merchant_code,
        "redirect_url": redirect_url,
        "return_url": return_url,
        "hosted_checkout": {"enabled": True},
    }
    result = _request(settings, "POST", "/v0.1/checkouts", payload)
    checkout_id = str(result.get("id") or "")
    checkout_url = str(result.get("hosted_checkout_url") or "")
    if not checkout_id or not checkout_url:
        raise SumUpError("SumUp did not return a hosted checkout URL")
    return HostedCheckout(checkout_id=checkout_id, checkout_url=checkout_url, status=str(result.get("status") or "PENDING"))


def retrieve_checkout(settings, checkout_id: str) -> dict:
    if not checkout_id:
        raise SumUpError("Missing SumUp checkout ID")
    return _request(settings, "GET", f"/v0.1/checkouts/{checkout_id}")


def order_amount_pence(order: CloudOrder) -> int:
    # Public Cloud checkout pre-calculates this using the mirrored event price
    # list, including multibuy offers. Synced legacy orders still fall back to
    # item arithmetic below.
    if int(order.payment_amount_pence or 0) > 0:
        return int(order.payment_amount_pence)
    try:
        items = json.loads(order.order_items_json or "[]")
    except json.JSONDecodeError:
        items = []
    subtotal = 0
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            subtotal += max(1, int(item.get("quantity", 1))) * max(0, int(item.get("unit_price_pence", 0)))
    return subtotal + max(0, int(order.delivery_charge_pence or 0))


def attach_checkout(session: Session, settings, orders: list[CloudOrder], *, checkout_reference: str, redirect_url: str, return_url: str, brand_name: str = "Photography") -> HostedCheckout:
    real_amount_pence = sum(
        order_amount_pence(order)
        for order in orders
    )

    amount_pence = (
        int(settings.payment_test_amount_pence)
        if settings.payment_test_mode
        else real_amount_pence
    )
    checkout = create_hosted_checkout(
        settings,
        reference=checkout_reference,
        amount_pence=amount_pence,
        description=f"{(brand_name or 'Photography').strip()} order {checkout_reference}",
        redirect_url=redirect_url,
        return_url=return_url,
    )
    for order in orders:
        order.payment_amount_pence = order_amount_pence(order)
        order.sumup_checkout_id = checkout.checkout_id
        order.sumup_checkout_url = checkout.checkout_url
        order.sumup_checkout_reference = checkout_reference[:90]
        order.sumup_status = checkout.status.upper()
        order.payment_status = "awaiting_payment"
        order.status = "awaiting_payment"
        order.fulfilment_error = ""
        record_order_audit(session, order, "payment_checkout_created", old_status="", new_status=order.status, detail=f"SumUp checkout {checkout.checkout_id}")
    session.commit()
    return checkout


def verify_and_apply_checkout(session: Session, settings, checkout_id: str) -> list[CloudOrder]:
    result = retrieve_checkout(settings, checkout_id)
    orders = list(session.scalars(select(CloudOrder).where(CloudOrder.sumup_checkout_id == checkout_id)))
    if not orders:
        raise SumUpError("No Pirouette orders match this SumUp checkout")

    status = str(result.get("status") or "").upper()
    actual_currency = str(result.get("currency") or "").upper()
    expected_pence = (
        int(settings.payment_test_amount_pence)
        if settings.payment_test_mode
        else sum(
            int(order.payment_amount_pence or 0)
            for order in orders
        )
    )
    try:
        actual_pence = int((Decimal(str(result.get("amount"))) * Decimal(100)).quantize(Decimal("1")))
    except Exception as exc:
        raise SumUpError("SumUp returned an invalid checkout amount") from exc
    if actual_currency != settings.sumup_currency or actual_pence != expected_pence:
        LOGGER.error("Rejected SumUp verification for %s because amount or currency did not match", checkout_id)
        raise SumUpError("SumUp checkout amount or currency did not match the Pirouette order")

    transaction_code = ""
    transactions = result.get("transactions")
    if isinstance(transactions, list):
        for transaction in transactions:
            if isinstance(transaction, dict) and transaction.get("transaction_code"):
                transaction_code = str(transaction["transaction_code"])
                break

    from datetime import datetime, timezone
    for order in orders:
        old_status = order.status
        order.sumup_status = status
        order.sumup_transaction_code = transaction_code
        if status == "PAID":
            order.payment_status = "paid"
            order.payment_verified_at = datetime.now(timezone.utc)
        elif status == "FAILED":
            order.payment_status = "failed"
            order.status = "payment_failed"
        elif status == "EXPIRED":
            order.payment_status = "expired"
            order.status = "payment_expired"
        else:
            order.payment_status = "awaiting_payment"
            order.status = "awaiting_payment"
        record_order_audit(session, order, "payment_verified", old_status=old_status, new_status=order.status, detail=f"SumUp status {status}")
    session.commit()

    if status == "PAID":
        orders = [process_paid_order(session, settings, order) for order in orders]
    return orders
