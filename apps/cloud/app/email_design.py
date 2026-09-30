from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote

_URL_RE = re.compile(r"(https?://[^\s<]+)")
STATIC_IMAGES = Path(__file__).resolve().parent / "static" / "images"
SUPPORT_EMAIL = "photos@sophiesphotography.co.uk"
CONTACT_URL = "https://www.sophies.photography/contact"


def sophies_logo_path() -> Path | None:
    candidates = (
        STATIC_IMAGES / "sophies-dashboard-logo.png",
        STATIC_IMAGES / "sophies-logo.png",
        STATIC_IMAGES / "logo.png",
    )
    return next((p for p in candidates if p.is_file()), None)


def brand_logo_path(brand: dict | None) -> Path | None:
    brand = brand or {}

    logo_url = str(
        brand.get("logo_url") or ""
    ).strip()

    if logo_url:
        candidate = STATIC_IMAGES / Path(logo_url).name

        if candidate.is_file():
            return candidate

    return sophies_logo_path()


def _paragraphs(body: str) -> str:
    blocks = []
    for raw in str(body or "").replace("\r\n", "\n").split("\n\n"):
        text = html.escape(raw.strip())
        if not text:
            continue
        text = _URL_RE.sub(r'<a href="\1" style="color:#d71920;font-weight:700;word-break:break-all">\1</a>', text)
        text = text.replace("\n", "<br>")
        blocks.append(f'<p style="margin:0 0 18px;line-height:1.65;color:#2a2a2a;font-size:16px">{text}</p>')
    return "".join(blocks)


def marketing_links(
    email: str = "",
    brand: dict | None = None,
) -> tuple[str, str]:
    brand = brand or {}

    address = str(email or "").strip().lower()

    display_name = str(
        brand.get("display_name")
        or "Sophie’s Photography"
    )

    support_email = str(
        brand.get("sender_email")
        or SUPPORT_EMAIL
    )

    subscribe = (
        f"mailto:{support_email}"
        f"?subject={quote('Subscribe me to ' + display_name + ' news & offers')}"
        f"&body={quote('I would like to opt in to receive ' + display_name + ' news, event updates and offers by email.\n\nEmail address: ' + address)}"
    )

    unsubscribe = (
        f"mailto:{support_email}"
        f"?subject={quote('Unsubscribe me from ' + display_name + ' marketing emails')}"
        f"&body={quote('Please unsubscribe this email address from ' + display_name + ' marketing emails.\n\nEmail address: ' + address + '\n\nThis does not affect service emails about my orders, galleries or account.')}"
    )

    return subscribe, unsubscribe


def _controls(
    email: str,
    brand: dict | None = None,
) -> str:
    subscribe, unsubscribe = marketing_links(
        email,
        brand,
    )

    return f"""<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:22px;background:#fafafa;border:1px solid #e4e4e4;border-radius:10px"><tr><td style="padding:18px 20px"><strong>Marketing preferences</strong><div style="font-size:12px;line-height:1.55;color:#666;margin:7px 0 12px">This is a service email. Marketing is separate and optional.</div><a href="{html.escape(subscribe, quote=True)}" style="display:inline-block;background:#161616;color:#fff;text-decoration:none;font-weight:700;padding:9px 12px;border-radius:7px;margin:0 7px 7px 0">Sign up to news &amp; offers</a><a href="{html.escape(unsubscribe, quote=True)}" style="display:inline-block;background:#fff;color:#b50b11;text-decoration:none;font-weight:700;padding:8px 12px;border:1px solid #b50b11;border-radius:7px">Unsubscribe from marketing</a><div style="font-size:11px;line-height:1.5;color:#777;margin-top:7px">You can also reply with “unsubscribe”. This does not stop necessary service messages about purchases or services you requested.</div></td></tr></table>"""


def branded_email_html(
    subject: str,
    body: str,
    *,
    action_label: str = "",
    action_url: str = "",
    eyebrow: str = "",
    customer_email: str = "",
    include_marketing_controls: bool = False,
    logo_cid: str = "brand-logo",
    extra_html: str = "",
    brand: dict | None = None,
) -> str:
    brand = brand or {}

    display_name = str(
        brand.get("display_name")
        or "Sophie’s Photography"
    )

    support_email = str(
        brand.get("sender_email")
        or SUPPORT_EMAIL
    )

    brand_eyebrow = (
        eyebrow
        or display_name.upper()
    )

    accent = str(
        (brand.get("theme") or {}).get(
            "accent_colour"
        )
        or "#c90d14"
    )

    header_background = str(
        (brand.get("theme") or {}).get(
            "header_background"
        )
        or "#090909"
    )

    action = ""

    if action_label and action_url:
        action = (
            '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:26px 0 8px"><tr><td>'
            f'<a href="{html.escape(action_url, quote=True)}" style="display:inline-block;background:{html.escape(accent)};color:#fff;text-decoration:none;font-weight:800;padding:14px 22px;border-radius:8px">{html.escape(action_label)}</a>'
            '</td></tr></table>'
        )

    logo = (
        f'<img src="cid:{html.escape(logo_cid, quote=True)}" '
        f'alt="{html.escape(display_name)}" '
        'style="display:block;max-width:320px;width:78%;height:auto;margin:0 auto 18px">'
        if logo_cid
        else ""
    )

    controls = (
        _controls(
            customer_email,
            brand,
        )
        if include_marketing_controls
        else ""
    )

    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f2f2f2;font-family:Arial,Helvetica,sans-serif;color:#202020"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f2f2;padding:28px 12px"><tr><td align="center"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #ddd"><tr><td style="background:{html.escape(header_background)};padding:25px 30px;border-bottom:4px solid {html.escape(accent)};text-align:center">{logo}<div style="font-size:12px;letter-spacing:2px;font-weight:800;color:{html.escape(accent)}">{html.escape(brand_eyebrow)}</div><div style="font-size:27px;line-height:1.15;font-weight:800;color:#fff;margin-top:7px">{html.escape(str(subject or display_name))}</div></td></tr><tr><td style="padding:30px">{_paragraphs(body)}{extra_html}{action}{controls}</td></tr><tr><td style="background:#f7f7f7;border-top:1px solid #e5e5e5;padding:20px 30px;color:#666;font-size:13px;line-height:1.6">{html.escape(display_name)} · Technology by Stuphie Ltd<br>Stuphie Ltd is registered in England and Wales. Company No. 11894921. Registered Office: Unit 29 Highcroft Industrial Estate, Enterprise Road, Waterlooville, England, PO8 0BT.<br>Need help? Reply to this email or contact <a href="mailto:{html.escape(support_email, quote=True)}">{html.escape(support_email)}</a>.</td></tr></table></td></tr></table></body></html>"""


def attach_brand_logo(
    message,
    brand: dict | None = None,
) -> None:
    logo = brand_logo_path(brand)

    if not logo:
        return

    try:
        html_part = message.get_payload()[-1]

        html_part.add_related(
            logo.read_bytes(),
            maintype="image",
            subtype="png",
            cid="<brand-logo>",
            filename=logo.name,
            disposition="inline",
        )

    except Exception:
        return


def attach_sophies_logo(message) -> None:
    # Backwards-compatible wrapper for older email callers.
    attach_brand_logo(
        message,
        {
            "display_name":
                "Sophie’s Photography",
            "logo_url":
                "/static/images/sophies-dashboard-logo.png",
            "sender_email":
                SUPPORT_EMAIL,
        },
    )
