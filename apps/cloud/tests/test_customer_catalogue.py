from __future__ import annotations

import json
from types import SimpleNamespace

from app.checkout.catalogue import (
    basket_pricing,
    best_price,
    event_catalogue,
)
from app.checkout.service import (
    HOME_PRINT_DELIVERY_CHARGE_PENCE,
    checkout_options,
)


def _event(pricing_json="[]"):
    return SimpleNamespace(
        pricing_json=pricing_json,
        event_collection_available=True,
        service_desk_payments_enabled=True,
        status="live",
    )


def test_default_catalogue_matches_sophies_prices():
    catalogue = event_catalogue(_event())

    expected = {
        "print_5x7": 1200,
        "print_6x8": 1400,
        "print_6x9": 1600,
        "print_7x10": 1800,
        "print_8x10": 2000,
        "print_8x12": 2200,
        "digital_social": 1400,
        "digital_highres": 2200,
    }

    for code, price in expected.items():
        assert catalogue[code]["price_pence"] == price

    assert (
        catalogue["digital_social"]["semantic_type"]
        == "low_res"
    )
    assert (
        catalogue["digital_highres"]["semantic_type"]
        == "high_res"
    )


def test_social_media_multibuy_prices():
    catalogue = event_catalogue(_event())
    social = catalogue["digital_social"]

    assert best_price(
        1,
        social["price_pence"],
        social["offers"],
    ) == 1400

    assert best_price(
        5,
        social["price_pence"],
        social["offers"],
    ) == 5500

    assert best_price(
        10,
        social["price_pence"],
        social["offers"],
    ) == 10000

    assert best_price(
        15,
        social["price_pence"],
        social["offers"],
    ) == 14500

    assert best_price(
        20,
        social["price_pence"],
        social["offers"],
    ) == 17500


def test_social_media_multibuy_optimises_mixed_quantity():
    catalogue = event_catalogue(_event())
    social = catalogue["digital_social"]

    assert best_price(
        6,
        social["price_pence"],
        social["offers"],
    ) == 6900

    assert best_price(
        11,
        social["price_pence"],
        social["offers"],
    ) == 11400

    assert best_price(
        21,
        social["price_pence"],
        social["offers"],
    ) == 18900


def test_basket_pricing_uses_catalogue_offers():
    catalogue = event_catalogue(_event())

    basket = [
        {
            "product_code": "digital_social",
            "quantity": 1,
        }
        for _ in range(5)
    ]

    total, totals = basket_pricing(
        basket,
        catalogue,
    )

    assert total == 5500
    assert totals["digital_social"] == 5500


def test_event_specific_catalogue_remains_authoritative():
    pricing = json.dumps(
        [
            {
                "code": "event_special",
                "category": "Prints",
                "name": "Event Special",
                "label": "Event Special",
                "price_pence": 9900,
                "fulfilment_type": "print",
                "sort_order": 1,
                "offers": [],
            }
        ]
    )

    catalogue = event_catalogue(
        _event(pricing)
    )

    assert "event_special" in catalogue
    assert catalogue["event_special"]["price_pence"] == 9900

    assert "print_5x7" not in catalogue

    assert "favourites_extension" in catalogue
    assert "favourites_extension_70" in catalogue


def test_print_home_delivery_is_seven_pounds():
    assert HOME_PRINT_DELIVERY_CHARGE_PENCE == 700

    options = checkout_options(
        _event(),
        customer_at_event=False,
        product_type="print",
        wants_home_delivery=True,
    )

    assert options["delivery_charge_pence"] == 700
    assert options["delivery_charge_display"] == "£7.00"


def test_event_collection_has_no_postage():
    options = checkout_options(
        _event(),
        customer_at_event=True,
        product_type="print",
        wants_home_delivery=False,
    )

    assert options["delivery_charge_pence"] == 0
