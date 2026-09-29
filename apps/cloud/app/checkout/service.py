from __future__ import annotations

from app.db.models import Event

DIGITAL_TYPES = {"low_res", "high_res", "video"}
PRINT_TYPES = {"print"}
HOME_PRINT_DELIVERY_CHARGE_PENCE = 700

def checkout_options(event: Event, *, customer_at_event: bool, product_type: str, wants_home_delivery: bool) -> dict[str, object]:
    event_live = event.status == "live"
    service_desk = bool(event_live and customer_at_event and event.service_desk_payments_enabled)
    payment_methods = ["sumup"]
    if service_desk:
        payment_methods.insert(0, "service_desk")

    if product_type == "print":
        collection_allowed = bool(event_live and customer_at_event and event.event_collection_available and not wants_home_delivery)
        fulfilment = "event_collection" if collection_allowed else "home_delivery"
        address_required = fulfilment == "home_delivery"
        delivery_charge_pence = HOME_PRINT_DELIVERY_CHARGE_PENCE if address_required else 0
        initial_status = "awaiting_payment"
    elif product_type == "low_res":
        fulfilment = "automatic_low_res_delivery"
        address_required = False
        delivery_charge_pence = 0
        initial_status = "awaiting_payment"
    elif product_type == "high_res":
        fulfilment = "staff_high_res_preparation"
        address_required = False
        delivery_charge_pence = 0
        initial_status = "awaiting_payment"
    elif product_type == "video":
        fulfilment = "staff_video_fulfilment"
        address_required = False
        delivery_charge_pence = 0
        initial_status = "awaiting_payment"
    elif product_type in {"favourites_extension", "favourites_extension_70"}:
        fulfilment = product_type
        address_required = False
        delivery_charge_pence = 0
        initial_status = "awaiting_payment"
    else:
        raise ValueError("Unsupported product type")

    return {
        "event_live": event_live,
        "payment_methods": payment_methods,
        "fulfilment_method": fulfilment,
        "address_required": address_required,
        "delivery_charge_pence": delivery_charge_pence,
        "delivery_charge_display": f"£{delivery_charge_pence / 100:.2f}",
        "initial_status": initial_status,
    }

def paid_status(product_type: str, fulfilment_method: str) -> str:
    if product_type == "low_res":
        return "ready_for_automatic_delivery"
    if product_type == "high_res":
        return "awaiting_high_resolution_preparation"
    if product_type == "print" and fulfilment_method == "event_collection":
        return "awaiting_print_production"
    if product_type == "print":
        return "awaiting_dispatch"
    if product_type in {"favourites_extension", "favourites_extension_70"}:
        return "completed"
    return "awaiting_staff_fulfilment"
