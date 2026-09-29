from __future__ import annotations

from types import SimpleNamespace

from app.checkout.catalogue import basket_pricing, event_catalogue
from app.checkout.service import checkout_options, paid_status


def event(*, live=True, collection=True, desk=True):
    return SimpleNamespace(
        pricing_json="[]",
        status="live" if live else "closed",
        event_collection_available=collection,
        service_desk_payments_enabled=desk,
    )


def test_print_collection_contract():
    options = checkout_options(
        event(),
        customer_at_event=True,
        product_type="print",
        wants_home_delivery=False,
    )
    assert options["fulfilment_method"] == "event_collection"
    assert options["delivery_charge_pence"] == 0
    assert options["address_required"] is False
    assert "service_desk" in options["payment_methods"]
    assert "sumup" in options["payment_methods"]


def test_print_postage_contract():
    options = checkout_options(
        event(),
        customer_at_event=False,
        product_type="print",
        wants_home_delivery=True,
    )
    assert options["fulfilment_method"] == "home_delivery"
    assert options["delivery_charge_pence"] == 700
    assert options["address_required"] is True
    assert options["payment_methods"] == ["sumup"]


def test_social_media_delivery_contract():
    options = checkout_options(
        event(),
        customer_at_event=True,
        product_type="low_res",
        wants_home_delivery=False,
    )
    assert options["fulfilment_method"] == "automatic_low_res_delivery"
    assert options["delivery_charge_pence"] == 0
    assert options["address_required"] is False
    assert paid_status("low_res", options["fulfilment_method"]) == "ready_for_automatic_delivery"


def test_high_resolution_delivery_contract():
    options = checkout_options(
        event(),
        customer_at_event=True,
        product_type="high_res",
        wants_home_delivery=False,
    )
    assert options["fulfilment_method"] == "staff_high_res_preparation"
    assert options["delivery_charge_pence"] == 0
    assert options["address_required"] is False
    assert paid_status("high_res", options["fulfilment_method"]) == "awaiting_high_resolution_preparation"


def test_print_paid_statuses():
    assert paid_status("print", "event_collection") == "awaiting_print_production"
    assert paid_status("print", "home_delivery") == "awaiting_dispatch"


def test_closed_event_does_not_offer_service_desk():
    options = checkout_options(
        event(live=False),
        customer_at_event=False,
        product_type="print",
        wants_home_delivery=True,
    )
    assert options["payment_methods"] == ["sumup"]
    assert options["fulfilment_method"] == "home_delivery"


def test_five_social_media_files_use_multibuy():
    catalogue = event_catalogue(event())
    basket = [
        {
            "product_code": "digital_social",
            "product_type": "low_res",
            "quantity": 1,
        }
        for _ in range(5)
    ]
    total, totals = basket_pricing(basket, catalogue)
    assert total == 5500
    assert totals["digital_social"] == 5500


def test_mixed_print_and_social_subtotals():
    catalogue = event_catalogue(event())
    basket = [
        {
            "product_code": "print_5x7",
            "product_type": "print",
            "quantity": 1,
        },
        {
            "product_code": "digital_social",
            "product_type": "low_res",
            "quantity": 1,
        },
    ]
    total, totals = basket_pricing(basket, catalogue)
    assert totals["print_5x7"] == 1200
    assert totals["digital_social"] == 1400
    assert total == 2600
