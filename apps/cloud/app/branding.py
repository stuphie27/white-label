from __future__ import annotations

BRANDS = {
    "sophies": {
        "brand_id": "sophies",
        "display_name": "Sophie's Photography",
        "logo_url": "/static/images/sophies-dashboard-logo.png",
        "public_host": "live.sophies.photography",
        "sender_name": "Sophie's Photography",
        "sender_email": "photos@sophiesphotography.co.uk",
        "theme": {
            "page_background": "#180305",
            "header_background": "#090102",
            "panel_background": "#2a070b",
            "panel_secondary": "#200407",
            "accent_colour": "#b20d1d",
            "accent_hover": "#e31b2f",
            "text_colour": "#ffffff",
            "muted_text_colour": "#d5b9bc",
            "border_colour": "#64202a",
        },
    },
    "partner": {
        "brand_id": "partner",
        "display_name": "DSI",
        "logo_url": "/static/images/DSI.png",
        "public_host": "photos.dsi-london.video",
        "sender_name": "DSI",
        "sender_email": "photos@dsi-london.video",
        "theme": {
            "page_background": "#031b3d",
            "header_background": "#011a38",
            "panel_background": "#003b70",
            "panel_secondary": "#001e45",
            "accent_colour": "#1599ef",
            "accent_hover": "#35baff",
            "text_colour": "#ffffff",
            "muted_text_colour": "#a9d5f0",
            "border_colour": "#399fdc",
        },
    },
}


def get_brand(brand_id: str | None) -> dict:
    key = str(brand_id or "sophies").strip() or "sophies"
    return BRANDS.get(key, BRANDS["sophies"])


def get_event_brand(event) -> dict:
    return get_brand(getattr(event, "brand_id", "sophies"))
