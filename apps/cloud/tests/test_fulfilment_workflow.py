from __future__ import annotations

from types import SimpleNamespace

from app.checkout.fulfilment import process_paid_order


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.refreshes = 0

    def commit(self):
        self.commits += 1

    def refresh(self, value):
        self.refreshes += 1


def order(
    *,
    product_type,
    fulfilment_method,
    status="paid",
):
    return SimpleNamespace(
        id="order-test",
        source_ref="cloud:test",
        order_reference="WEB-TEST",
        product_type=product_type,
        fulfilment_method=fulfilment_method,
        payment_status="paid",
        status=status,
        fulfilment_error="",
        retry_count=0,
        completed_at=None,
        delivery_id=None,
    )


def test_high_res_paid_order_stops_for_staff(
    monkeypatch,
):
    current = order(
        product_type="high_res",
        fulfilment_method="staff_high_res_preparation",
    )
    session = FakeSession()

    monkeypatch.setattr(
        "app.checkout.fulfilment.record_order_audit",
        lambda *args, **kwargs: None,
    )

    result = process_paid_order(
        session,
        SimpleNamespace(),
        current,
    )

    assert result.status == (
        "awaiting_high_resolution_preparation"
    )
    assert result.payment_status == "paid"
    assert session.commits == 1


def test_collection_print_routes_to_print_production(
    monkeypatch,
):
    current = order(
        product_type="print",
        fulfilment_method="event_collection",
    )
    session = FakeSession()

    monkeypatch.setattr(
        "app.checkout.fulfilment.record_order_audit",
        lambda *args, **kwargs: None,
    )

    result = process_paid_order(
        session,
        SimpleNamespace(),
        current,
    )

    assert result.status == "awaiting_print_production"


def test_postal_print_routes_to_print_production_first(
    monkeypatch,
):
    current = order(
        product_type="print",
        fulfilment_method="home_delivery",
    )
    session = FakeSession()

    monkeypatch.setattr(
        "app.checkout.fulfilment.record_order_audit",
        lambda *args, **kwargs: None,
    )

    result = process_paid_order(
        session,
        SimpleNamespace(),
        current,
    )

    assert result.status == (
        "awaiting_print_production_for_dispatch"
    )
