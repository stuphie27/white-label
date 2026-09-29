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


def marketing_links(email: str = "") -> tuple[str, str]:
    address = str(email or "").strip().lower()
    subscribe = f"mailto:{SUPPORT_EMAIL}?subject={quote('Subscribe me to Sophie’s Photography news & offers')}&body={quote('I would like to opt in to receive Sophie’s Photography news, event updates and offers by email.\n\nEmail address: ' + address)}"
    unsubscribe = f"mailto:{SUPPORT_EMAIL}?subject={quote('Unsubscribe me from Sophie’s Photography marketing emails')}&body={quote('Please unsubscribe this email address from Sophie’s Photography marketing emails.\n\nEmail address: ' + address + '\n\nThis does not affect service emails about my orders, galleries or account.')}"
    return subscribe, unsubscribe


def _controls(email: str) -> str:
    subscribe, unsubscribe = marketing_links(email)
    return f'''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:22px;background:#fafafa;border:1px solid #e4e4e4;border-radius:10px"><tr><td style="padding:18px 20px"><strong>Marketing preferences</strong><div style="font-size:12px;line-height:1.55;color:#666;margin:7px 0 12px">This is a service email. Marketing is separate and optional.</div><a href="{html.escape(subscribe, quote=True)}" style="display:inline-block;background:#161616;color:#fff;text-decoration:none;font-weight:700;padding:9px 12px;border-radius:7px;margin:0 7px 7px 0">Sign up to news &amp; offers</a><a href="{html.escape(unsubscribe, quote=True)}" style="display:inline-block;background:#fff;color:#b50b11;text-decoration:none;font-weight:700;padding:8px 12px;border:1px solid #b50b11;border-radius:7px">Unsubscribe from marketing</a><div style="font-size:11px;line-height:1.5;color:#777;margin-top:7px">You can also reply with “unsubscribe”. This does not stop necessary service messages about purchases or services you requested.</div></td></tr></table>'''


def branded_email_html(subject: str, body: str, *, action_label: str = "", action_url: str = "", eyebrow: str = "SOPHIE’S PHOTOGRAPHY", customer_email: str = "", include_marketing_controls: bool = False, logo_cid: str = "sophies-logo", extra_html: str = "") -> str:
    action = ""
    if action_label and action_url:
        action = ('<table role="presentation" cellspacing="0" cellpadding="0" style="margin:26px 0 8px"><tr><td>'
                  f'<a href="{html.escape(action_url, quote=True)}" style="display:inline-block;background:#c90d14;color:#fff;text-decoration:none;font-weight:800;padding:14px 22px;border-radius:8px">{html.escape(action_label)}</a>'
                  '</td></tr></table>')
    logo = f'<img src="cid:{html.escape(logo_cid, quote=True)}" alt="Sophie’s Photography" style="display:block;max-width:320px;width:78%;height:auto;margin:0 auto 18px">' if logo_cid else ""
    controls = _controls(customer_email) if include_marketing_controls else ""
    return f'''<!doctype html><html><body style="margin:0;padding:0;background:#f2f2f2;font-family:Arial,Helvetica,sans-serif;color:#202020"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f2f2;padding:28px 12px"><tr><td align="center"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #ddd"><tr><td style="background:#090909;padding:25px 30px;border-bottom:4px solid #c90d14;text-align:center">{logo}<div style="font-size:12px;letter-spacing:2px;font-weight:800;color:#ef4444">{html.escape(eyebrow)}</div><div style="font-size:27px;line-height:1.15;font-weight:800;color:#fff;margin-top:7px">{html.escape(str(subject or 'Sophie’s Photography'))}</div></td></tr><tr><td style="padding:30px">{_paragraphs(body)}{extra_html}{action}{controls}</td></tr><tr><td style="background:#f7f7f7;border-top:1px solid #e5e5e5;padding:20px 30px;color:#666;font-size:13px;line-height:1.6">Sophie’s Photography · Technology by Stuphie Ltd<br>Stuphie Ltd is registered in England and Wales. Company No. 11894921. Registered Office: Unit 29 Highcroft Industrial Estate, Enterprise Road, Waterlooville, England, PO8 0BT.<br>Need help? Reply to this email or contact <a href="mailto:{SUPPORT_EMAIL}" style="color:#b50b11">{SUPPORT_EMAIL}</a> · <a href="{CONTACT_URL}" style="color:#b50b11">Contact us</a>.</td></tr></table></td></tr></table></body></html>'''


def attach_sophies_logo(message) -> None:
    logo = sophies_logo_path()
    if not logo:
        return
    try:
        html_part = message.get_payload()[-1]
        html_part.add_related(logo.read_bytes(), maintype="image", subtype="png", cid="<sophies-logo>", filename=logo.name, disposition="inline")
    except Exception:
        return
