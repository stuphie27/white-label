from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.checkout.service import paid_status
from app.payments import sumup


class FakeScalarResult:
    def __init__(self, orders):
        self.orders = orders

    def all(self):
        return self.orders

    def __iter__(self):
        return iter(self.orders)


class FakeSession:
    def __init__(self, orders):
        self.orders = orders
        self.commits = 0

    def scalars(self, statement):
        return FakeScalarResult(self.orders)

    def commit(self):
        self.commits += 1


def settings():
    return SimpleNamespace(
        sumup_currency="GBP",
    )


def order(
    *,
    product_type="print",
    fulfilment_method="event_collection",
    amount=1200,
):
    return SimpleNamespace(
        id="order-1",
        order_reference="WEB-TEST",
        product_type=product_type,
        fulfilment_method=fulfilment_method,
        payment_method="sumup",
        payment_status="awaiting_payment",
        payment_amount_pence=amount,
        sumup_checkout_id="checkout-1",
        sumup_checkout_url="https://example.invalid/pay",
        sumup_checkout_reference="WEB-TEST",
        sumup_status="PENDING",
        sumup_transaction_code="",
        payment_verified_at=None,
        status="checkout_pending",
        fulfilment_error="",
    )


@pytest.mark.parametrize(
    ("product_type", "fulfilment", "expected"),
    [
        ("low_res", "automatic_low_res_delivery", "ready_for_automatic_delivery"),
        ("high_res", "staff_high_res_preparation", "awaiting_high_resolution_preparation"),
        ("print", "event_collection", "awaiting_print_production"),
        ("print", "home_delivery", "awaiting_dispatch"),
        ("favourites_extension", "favourites_extension", "completed"),
        ("favourites_extension_70", "favourites_extension_70", "completed"),
    ],
)
def test_paid_status_routes_products_to_correct_fulfilment(
    product_type,
    fulfilment,
    expected,
):
    assert paid_status(product_type, fulfilment) == expected


def test_sumup_paid_is_verified_before_fulfilment(monkeypatch):
    current = order()
    session = FakeSession([current])
    processed = []

    monkeypatch.setattr(
        sumup,
        "retrieve_checkout",
        lambda settings, checkout_id: {
            "id": checkout_id,
            "status": "PAID",
            "currency": "GBP",
            "amount": "12.00",
            "transactions": [{"transaction_code": "TX-TEST"}],
        },
    )
    monkeypatch.setattr(
        sumup,
        "record_order_audit",
        lambda *args, **kwargs: None,
    )

    def fake_process_paid_order(session, settings, paid_order):
        processed.append(paid_order.id)
        paid_order.status = paid_status(
            paid_order.product_type,
            paid_order.fulfilment_method,
        )
        return paid_order

    monkeypatch.setattr(
        sumup,
        "process_paid_order",
        fake_process_paid_order,
    )

    result = sumup.verify_and_apply_checkout(
        session,
        settings(),
        "checkout-1",
    )

    assert result == [current]
    assert current.payment_status == "paid"
    assert current.sumup_status == "PAID"
    assert current.sumup_transaction_code == "TX-TEST"
    assert current.payment_verified_at is not None
    assert current.status == "awaiting_print_production"
    assert processed == ["order-1"]


@pytest.mark.parametrize(
    ("sumup_status", "payment_status", "order_status"),
    [
        ("FAILED", "failed", "payment_failed"),
        ("EXPIRED", "expired", "payment_expired"),
        ("PENDING", "awaiting_payment", "awaiting_payment"),
    ],
)
def test_non_paid_sumup_states_never_fulfil(
    monkeypatch,
    sumup_status,
    payment_status,
    order_status,
):
    current = order()
    session = FakeSession([current])
    processed = []

    monkeypatch.setattr(
        sumup,
        "retrieve_checkout",
        lambda settings, checkout_id: {
            "id": checkout_id,
            "status": sumup_status,
            "currency": "GBP",
            "amount": "12.00",
            "transactions": [],
        },
    )
    monkeypatch.setattr(
        sumup,
        "record_order_audit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        sumup,
        "process_paid_order",
        lambda *args, **kwargs: processed.append("BAD"),
    )

    result = sumup.verify_and_apply_checkout(
        session,
        settings(),
        "checkout-1",
    )

    assert result == [current]
    assert current.payment_status == payment_status
    assert current.status == order_status
    assert processed == []


@pytest.mark.parametrize(
    ("amount", "currency"),
    [
        ("11.99", "GBP"),
        ("12.01", "GBP"),
        ("12.00", "EUR"),
    ],
)
def test_sumup_amount_or_currency_mismatch_is_rejected_before_fulfilment(
    monkeypatch,
    amount,
    currency,
):
    current = order()
    session = FakeSession([current])
    processed = []

    monkeypatch.setattr(
        sumup,
        "retrieve_checkout",
        lambda settings, checkout_id: {
            "id": checkout_id,
            "status": "PAID",
            "currency": currency,
            "amount": amount,
            "transactions": [{"transaction_code": "TX-BAD"}],
        },
    )
    monkeypatch.setattr(
        sumup,
        "process_paid_order",
        lambda *args, **kwargs: processed.append("BAD"),
    )

    with pytest.raises(
        sumup.SumUpError,
        match="amount or currency",
    ):
        sumup.verify_and_apply_checkout(
            session,
            settings(),
            "checkout-1",
        )

    assert current.payment_status == "awaiting_payment"
    assert current.status == "checkout_pending"
    assert current.payment_verified_at is None
    assert processed == []


def test_pay_at_stand_paid_status_contract():
    assert (
        paid_status("print", "event_collection")
        == "awaiting_print_production"
    )
    assert (
        paid_status("low_res", "automatic_low_res_delivery")
        == "ready_for_automatic_delivery"
    )
    assert (
        paid_status("high_res", "staff_high_res_preparation")
        == "awaiting_high_resolution_preparation"
    )
