from __future__ import annotations

import json
from collections import defaultdict


DEFAULT_CATALOGUE = {
    "print_5x7": {
        "code": "print_5x7",
        "category": "Prints",
        "name": "5×7 Print",
        "label": "5×7 Print",
        "price_pence": 1200,
        "fulfilment_type": "print",
        "semantic_type": "print",
        "quick_checkout": True,
        "sold_out": False,
        "sort_order": 10,
        "offers": [],
    },
    "print_6x8": {
        "code": "print_6x8",
        "category": "Prints",
        "name": "6×8 Print",
        "label": "6×8 Print",
        "price_pence": 1400,
        "fulfilment_type": "print",
        "semantic_type": "print",
        "quick_checkout": True,
        "sold_out": False,
        "sort_order": 20,
        "offers": [],
    },
    "print_6x9": {
        "code": "print_6x9",
        "category": "Prints",
        "name": "6×9 Print",
        "label": "6×9 Print",
        "price_pence": 1600,
        "fulfilment_type": "print",
        "semantic_type": "print",
        "quick_checkout": True,
        "sold_out": False,
        "sort_order": 30,
        "offers": [],
    },
    "print_7x10": {
        "code": "print_7x10",
        "category": "Prints",
        "name": "7×10 Print",
        "label": "7×10 Print",
        "price_pence": 1800,
        "fulfilment_type": "print",
        "semantic_type": "print",
        "quick_checkout": True,
        "sold_out": False,
        "sort_order": 40,
        "offers": [],
    },
    "print_8x10": {
        "code": "print_8x10",
        "category": "Prints",
        "name": "8×10 Print",
        "label": "8×10 Print",
        "price_pence": 2000,
        "fulfilment_type": "print",
        "semantic_type": "print",
        "quick_checkout": True,
        "sold_out": False,
        "sort_order": 50,
        "offers": [],
    },
    "print_8x12": {
        "code": "print_8x12",
        "category": "Prints",
        "name": "8×12 Print",
        "label": "8×12 Print",
        "price_pence": 2200,
        "fulfilment_type": "print",
        "semantic_type": "print",
        "quick_checkout": True,
        "sold_out": False,
        "sort_order": 60,
        "offers": [],
    },
    "digital_social": {
        "code": "digital_social",
        "category": "Digital",
        "name": "Social Media Digital",
        "label": "Social Media Digital",
        "price_pence": 1400,
        "fulfilment_type": "digital",
        "semantic_type": "low_res",
        "quick_checkout": True,
        "sold_out": False,
        "sort_order": 70,
        "offers": [
            {
                "quantity": 5,
                "price_pence": 5500,
                "label": "5 for £55",
            },
            {
                "quantity": 10,
                "price_pence": 10000,
                "label": "10 for £100",
            },
            {
                "quantity": 15,
                "price_pence": 14500,
                "label": "15 for £145",
            },
            {
                "quantity": 20,
                "price_pence": 17500,
                "label": "20 for £175",
            },
        ],
        "note": (
            "Clean digital photograph prepared for "
            "personal social media use."
        ),
    },
    "digital_highres": {
        "code": "digital_highres",
        "category": "Digital",
        "name": "High Resolution Digital",
        "label": "High Resolution Digital",
        "price_pence": 2200,
        "fulfilment_type": "digital",
        "semantic_type": "high_res",
        "quick_checkout": True,
        "sold_out": False,
        "sort_order": 80,
        "offers": [],
        "note": (
            "High-resolution digital photograph suitable "
            "for personal printing up to A3."
        ),
    },
}


FAVOURITES_EXTENSION = {
    "code": "favourites_extension",
    "category": "Favourites",
    "name": "35-day favourites extension",
    "label": "35-day favourites extension",
    "price_pence": 1500,
    "fulfilment_type": "digital",
    "semantic_type": "favourites_extension",
    "quick_checkout": False,
    "sold_out": False,
    "sort_order": 9998,
    "offers": [],
    "note": (
        "Keep this event favourites folder available "
        "for 35 days after the event closes."
    ),
}


FAVOURITES_EXTENSION_70 = {
    "code": "favourites_extension_70",
    "category": "Favourites",
    "name": "70-day favourites extension",
    "label": "70-day favourites extension",
    "price_pence": 2500,
    "fulfilment_type": "digital",
    "semantic_type": "favourites_extension_70",
    "quick_checkout": False,
    "sold_out": False,
    "sort_order": 9999,
    "offers": [],
    "note": (
        "Keep this event favourites folder available "
        "for 70 days after the event closes."
    ),
}


def _semantic_type(product: dict) -> str:
    code = str(product.get("code") or "").lower()
    fulfil = str(
        product.get("fulfilment_type") or ""
    ).lower()
    name = str(product.get("name") or "").lower()

    if code.startswith("print_") or fulfil == "print":
        return "print"

    if "high" in code and (
        "res" in code or "resolution" in name
    ):
        return "high_res"

    if fulfil == "digital" or code.startswith("digital_"):
        return "low_res"

    return str(
        product.get("semantic_type") or "print"
    )


def event_catalogue(event) -> dict[str, dict]:
    try:
        rows = json.loads(
            getattr(event, "pricing_json", "") or "[]"
        )
    except (TypeError, ValueError):
        rows = []

    catalogue = {}

    if isinstance(rows, list):
        for raw in rows:
            if not isinstance(raw, dict):
                continue

            product = dict(raw)
            code = str(
                product.get("code") or ""
            ).strip()

            if not code:
                continue

            product["code"] = code
            product["semantic_type"] = _semantic_type(
                product
            )
            product["price_pence"] = max(
                0,
                int(product.get("price_pence") or 0),
            )
            product["offers"] = (
                product.get("offers")
                if isinstance(
                    product.get("offers"),
                    list,
                )
                else []
            )
            product["sold_out"] = bool(
                product.get("sold_out", False)
            )
            product["sort_order"] = int(
                product.get("sort_order") or 0
            )
            product["label"] = str(
                product.get("label")
                or product.get("name")
                or code
            )
            product["name"] = str(
                product.get("name")
                or product["label"]
            )
            product["note"] = str(
                product.get("note")
                or (
                    "Digital purchase delivered privately "
                    "without the gallery watermark."
                    if product["semantic_type"]
                    in {"low_res", "high_res"}
                    else (
                        "Print/gift product from the "
                        "event price list."
                    )
                )
            )

            catalogue[code] = product

    if not catalogue:
        catalogue = {
            code: dict(product)
            for code, product
            in DEFAULT_CATALOGUE.items()
        }

    catalogue[
        FAVOURITES_EXTENSION["code"]
    ] = dict(FAVOURITES_EXTENSION)

    catalogue[
        FAVOURITES_EXTENSION_70["code"]
    ] = dict(FAVOURITES_EXTENSION_70)

    return dict(
        sorted(
            catalogue.items(),
            key=lambda item: (
                int(
                    item[1].get(
                        "sort_order",
                        0,
                    )
                ),
                item[0],
            ),
        )
    )


def best_price(
    quantity: int,
    unit_price_pence: int,
    offers: list[dict],
) -> int:
    quantity = max(0, int(quantity))

    choices = [
        (1, int(unit_price_pence)),
    ]

    for offer in offers:
        try:
            offer_quantity = int(
                offer["quantity"]
            )
            offer_price = int(
                offer.get("price_pence") or 0
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

        if offer_quantity > 0 and offer_price >= 0:
            choices.append(
                (
                    offer_quantity,
                    offer_price,
                )
            )

    dp = [0] + [10**18] * quantity

    for count in range(1, quantity + 1):
        for pack_quantity, pack_price in choices:
            if pack_quantity <= count:
                dp[count] = min(
                    dp[count],
                    dp[count - pack_quantity]
                    + pack_price,
                )

    return int(dp[quantity])


def basket_pricing(
    items: list[dict],
    catalogue: dict[str, dict],
) -> tuple[int, dict[str, int]]:
    quantities = defaultdict(int)

    for item in items:
        code = str(
            item.get("product_code")
            or item.get("product_type")
            or ""
        )
        quantities[code] += max(
            1,
            int(item.get("quantity") or 1),
        )

    totals = {}
    total = 0

    for code, quantity in quantities.items():
        product = catalogue.get(code)

        if not product:
            continue

        value = best_price(
            quantity,
            int(product.get("price_pence") or 0),
            product.get("offers") or [],
        )

        totals[code] = value
        total += value

    return total, totals
