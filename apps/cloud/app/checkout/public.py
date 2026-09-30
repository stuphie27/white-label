from __future__ import annotations

import json
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.checkout.service import checkout_options
from app.checkout.catalogue import event_catalogue, basket_pricing
from app.customer_ownership import (
    favourite_session_for_verified_customer,
    verified_customer_profile,
)
from app.db.models import CloudOrder, CustomerFavourite, CustomerFavouriteSession, Event, GalleryAsset
from app.branding import get_event_brand
from app.db.models import CustomerRewardSnapshot
from app.gallery.service import get_gallery_by_slug
from app.payments.sumup import SumUpError, attach_checkout, customer_safe_error, verify_and_apply_checkout
from app.storage import presigned_get_url
from app.storage import client as spaces_client



def _gallery_available(gallery) -> bool:
    return bool(gallery and gallery.status == "published" and not (gallery.expires_at and gallery.expires_at < date.today()))


def _unlocked(request: Request, gallery) -> bool:
    return bool(gallery.visibility == "public" or request.session.get(f"gallery_access_{gallery.id}") is True)


def _basket_key(gallery_id: str) -> str:
    return f"customer_basket_{gallery_id}"


def _favourites_key(gallery_id: str) -> str:
    return f"customer_favourites_{gallery_id}"


def _basket(request: Request, gallery_id: str) -> list[dict]:
    value = request.session.get(_basket_key(gallery_id), [])
    return value if isinstance(value, list) else []


def _favourites(request: Request, gallery_id: str) -> list[str]:
    value = request.session.get(_favourites_key(gallery_id), [])
    return [str(item) for item in value] if isinstance(value, list) else []



def _favourite_token_key(gallery_id: str) -> str:
    return f"customer_favourites_token_{gallery_id}"


def _normalise_email(value: str) -> str:
    return str(value or "").strip().lower()[:320]


def _persistent_favourite_session(db, request: Request, gallery_id: str):
    token = str(request.session.get(_favourite_token_key(gallery_id), "") or "")
    if not token:
        return None
    return db.scalar(select(CustomerFavouriteSession).where(
        CustomerFavouriteSession.gallery_id == gallery_id,
        CustomerFavouriteSession.token == token,
    ))


def _persistent_favourite_ids(db, favourite_session) -> list[str]:
    if not favourite_session:
        return []
    return [str(v) for v in db.scalars(select(CustomerFavourite.asset_id).where(
        CustomerFavourite.session_id == favourite_session.id
    ).order_by(CustomerFavourite.created_at.desc()))]


def _remember_favourite_session(request: Request, gallery_id: str, favourite_session) -> None:
    request.session[_favourite_token_key(gallery_id)] = favourite_session.token
    request.session[f"customer_profile_{gallery_id}"] = {
        "name": favourite_session.customer_name or "",
        "email": favourite_session.email or "",
        "phone": str(request.session.get(f"customer_profile_{gallery_id}", {}).get("phone", "") or ""),
    }


def _set_extension_basket(request: Request, gallery_id: str, favourite_session, enabled: bool, days: int = 35) -> None:
    basket = [
        item for item in _basket(request, gallery_id)
        if item.get("product_type") not in {"favourites_extension", "favourites_extension_70"}
    ]
    if enabled:
        is_70 = int(days) == 70
        code = "favourites_extension_70" if is_70 else "favourites_extension"
        basket.append({
            "key": code, "asset_id": "", "asset_source_ref": "",
            "filename": "70-day favourites extension" if is_70 else "35-day favourites extension",
            "product_code": code,
            "product_type": code,
            "quantity": 1, "favourite_token": favourite_session.token,
        })
    request.session[_basket_key(gallery_id)] = basket


def _activate_paid_favourites_extensions(db, orders) -> None:
    changed = False
    now = datetime.now(timezone.utc)
    for order in orders:
        if order.payment_status != "paid":
            continue
        try:
            items = json.loads(order.order_items_json or "[]")
        except Exception:
            items = []
        for item in items:
            if item.get("product_type") not in {"favourites_extension", "favourites_extension_70"}:
                continue
            token = str(item.get("favourite_token") or "")
            fav = db.scalar(select(CustomerFavouriteSession).where(CustomerFavouriteSession.token == token)) if token else None
            if fav and not fav.extension_paid:
                extension_days = 70 if item.get("product_type") == "favourites_extension_70" else 35
                fav.extension_requested = True
                fav.extension_paid = True
                fav.retention_choice = "extension_70" if extension_days == 70 else "vault_year"
                fav.expires_at = now + timedelta(days=extension_days)
                changed = True
    if changed:
        db.commit()

def _csrf(request: Request) -> str:
    token = secrets.token_urlsafe(24)
    request.session["customer_checkout_csrf"] = token
    return token


def _valid_csrf(request: Request, supplied: str) -> bool:
    expected = str(request.session.get("customer_checkout_csrf", ""))
    return bool(expected and secrets.compare_digest(expected, supplied))


def _order_ref() -> str:
    return "WEB-" + secrets.token_hex(4).upper()


def build_public_checkout_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(tags=["customer-checkout"])

    @router.get("/g/{slug}/favourites", response_class=HTMLResponse, include_in_schema=False)
    async def favourites_page(request: Request, slug: str):
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            event = session.get(Event, gallery.event_id) if gallery else None
            favourite_session = None
            if gallery:
                favourite_session = favourite_session_for_verified_customer(
                    session,
                    request,
                    gallery,
                    create=False,
                )
                if favourite_session is None:
                    favourite_session = _persistent_favourite_session(
                        session,
                        request,
                        gallery.id,
                    )
                elif favourite_session:
                    _remember_favourite_session(
                        request,
                        gallery.id,
                        favourite_session,
                    )
            verified_profile = verified_customer_profile(session, request) if gallery else None
            favourite_ids = _persistent_favourite_ids(session, favourite_session) if favourite_session else _favourites(request, gallery.id) if gallery else []
            assets = list(session.scalars(select(GalleryAsset).where(
                GalleryAsset.gallery_id == gallery.id,
                GalleryAsset.status == "ready",
                GalleryAsset.id.in_(favourite_ids),
            ).order_by(GalleryAsset.created_at.desc()))) if gallery and favourite_ids else []
            # Private favourites used to load every thumbnail through the application
            # asset route. For Spaces-backed previews that meant an extra request, DB
            # lookup and redirect per photograph. Sign those object URLs once while
            # rendering the page so the browser can fetch the previews directly.
            favourite_asset_urls = {}
            spaces_assets = [asset for asset in assets if str(asset.storage_path or "").startswith("spaces://")]
            signer = spaces_client(request.app.state.settings) if spaces_assets else None
            for asset in assets:
                storage_location = str(asset.storage_path or "")
                if storage_location.startswith("spaces://"):
                    favourite_asset_urls[str(asset.id)] = presigned_get_url(
                        request.app.state.settings,
                        storage_location,
                        expires_seconds=900,
                        storage_client=signer,
                    )
                else:
                    favourite_asset_urls[str(asset.id)] = f"/g/{slug}/assets/{asset.id}"
        if not _gallery_available(gallery) or not _unlocked(request, gallery):
            return RedirectResponse(f"/g/{slug}", status_code=303)
        asset_by_id = {str(asset.id): asset for asset in assets}
        ordered_assets = [asset_by_id[item_id] for item_id in favourite_ids if item_id in asset_by_id]
        brand = get_event_brand(event)

        return templates.TemplateResponse(request=request, name="customer_favourites.html", context={
            "gallery": gallery, "event": event, "brand": brand,
            "assets": ordered_assets, "favourite_ids": set(favourite_ids),
            "profile": request.session.get(f"customer_profile_{gallery.id}", {}),
            "verified_customer": verified_profile is not None,
            "favourite_session": favourite_session,
            "protected_favourites": bool(gallery and request.session.get(f"protected_favourites_{gallery.id}")),
            "favourite_asset_urls": favourite_asset_urls,
            "product_catalogue": [
                product
                for code, product
                in event_catalogue(event).items()
                if code not in {
                    "favourites_extension",
                    "favourites_extension_70",
                }
                and not product.get("sold_out")
            ],
        })

    @router.get("/g/{slug}/favourites/open/{token}", include_in_schema=False)
    async def open_magic_favourites(
        request: Request,
        slug: str,
        token: str,
        upgrade: int = Query(default=0),
    ):
        """Open a customer-only favourites gallery from a public magic link."""
        with request.app.state.session_factory() as db:
            gallery = get_gallery_by_slug(db, slug)
            if not _gallery_available(gallery):
                return RedirectResponse(f"/g/{slug}", status_code=303)
            favourite_session = db.scalar(select(CustomerFavouriteSession).where(
                CustomerFavouriteSession.gallery_id == gallery.id,
                CustomerFavouriteSession.token == token,
            ))
            if favourite_session is None:
                return RedirectResponse(f"/g/{slug}", status_code=303)
            expires_at = favourite_session.expires_at
            if expires_at is not None and expires_at < datetime.now(timezone.utc):
                return RedirectResponse(f"/g/{slug}?error=" + quote("This favourites link has expired."), status_code=303)
            _remember_favourite_session(request, gallery.id, favourite_session)
            request.session[f"gallery_access_{gallery.id}"] = True
            request.session[f"protected_favourites_{gallery.id}"] = True
            if upgrade in {35, 70} and not favourite_session.extension_paid:
                favourite_session.extension_requested = True
                favourite_session.retention_choice = "extension_70" if upgrade == 70 else "vault_year"
                _set_extension_basket(request, gallery.id, favourite_session, True, days=upgrade)
                db.commit()
        target = f"/g/{slug}/favourites"
        if upgrade in {35, 70}:
            target += "?basket=open"
        return RedirectResponse(target, status_code=303)

    @router.post("/g/{slug}/favourites/session", include_in_schema=False)
    async def open_favourite_session(request: Request, slug: str, customer_name: str = Form(""), customer_email: str = Form(...), extend_35_days: str = Form("false"), reopen_only: str = Form("false")):
        with request.app.state.session_factory() as db:
            gallery = get_gallery_by_slug(db, slug)
            if not _gallery_available(gallery) or not _unlocked(request, gallery):
                return JSONResponse({"ok": False, "error": "Gallery unavailable"}, status_code=404)
            verified_profile = verified_customer_profile(db, request)
            if verified_profile is not None:
                existing_verified = favourite_session_for_verified_customer(
                    db,
                    request,
                    gallery,
                    create=False,
                )
                favourite_session = (
                    existing_verified
                    or favourite_session_for_verified_customer(
                        db,
                        request,
                        gallery,
                        create=True,
                    )
                )
                created = existing_verified is None
            else:
                email = _normalise_email(customer_email)
                name = str(customer_name or "").strip()[:200]
                if not email or "@" not in email:
                    return JSONResponse({"ok": False, "error": "Please enter a valid email address."}, status_code=400)
                favourite_session = db.scalar(select(CustomerFavouriteSession).where(
                    CustomerFavouriteSession.gallery_id == gallery.id,
                    CustomerFavouriteSession.email == email,
                ))
                created = favourite_session is None
                if favourite_session is None and str(reopen_only).lower() in {"1", "true", "yes", "on"}:
                    return JSONResponse({"ok": False, "error": "We could not find saved favourites for that email address."}, status_code=404)
                if favourite_session is None:
                    event = db.get(Event, gallery.event_id)
                    free_expiry = None if event and event.status == "live" else datetime.now(timezone.utc) + timedelta(days=5)
                    favourite_session = CustomerFavouriteSession(
                        gallery_id=gallery.id, token=secrets.token_urlsafe(32), customer_name=name, email=email,
                        retention_choice="temporary_link", expires_at=free_expiry,
                    )
                    db.add(favourite_session)
                elif name:
                    favourite_session.customer_name = name
            enabled = str(extend_35_days).lower() in {"1", "true", "yes", "on"}
            favourite_session.extension_requested = enabled or favourite_session.extension_requested
            if not favourite_session.extension_paid:
                favourite_session.retention_choice = "vault_year" if enabled else "temporary_link"
                favourite_session.expires_at = datetime.now(timezone.utc) + timedelta(days=5)
            db.commit(); db.refresh(favourite_session)
            count = db.scalar(select(CustomerFavourite).where(CustomerFavourite.session_id == favourite_session.id).count()) if False else len(_persistent_favourite_ids(db, favourite_session))
            _remember_favourite_session(request, gallery.id, favourite_session)
            if enabled and not favourite_session.extension_paid:
                _set_extension_basket(request, gallery.id, favourite_session, True)
            return JSONResponse({
                "ok": True, "created": created, "token": favourite_session.token,
                "name": favourite_session.customer_name, "email": favourite_session.email,
                "count": count, "url": f"/g/{slug}/favourites",
                "extension_in_basket": enabled and not favourite_session.extension_paid,
            })

    @router.post("/g/{slug}/favourites/{asset_id}", include_in_schema=False)
    async def toggle_favourite(request: Request, slug: str, asset_id: str, return_to: str = Form("")):
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            asset = session.get(GalleryAsset, asset_id) if gallery else None
        if not _gallery_available(gallery) or not asset or asset.gallery_id != gallery.id or not _unlocked(request, gallery):
            return RedirectResponse(f"/g/{slug}", status_code=303)
        with request.app.state.session_factory() as db:
            favourite_session = favourite_session_for_verified_customer(
                db,
                request,
                gallery,
                create=True,
            )
            if favourite_session is None:
                favourite_session = _persistent_favourite_session(
                    db,
                    request,
                    gallery.id,
                )
            else:
                _remember_favourite_session(
                    request,
                    gallery.id,
                    favourite_session,
                )
            if not favourite_session:
                if request.headers.get("X-Requested-With") == "PirouetteFavourite":
                    return JSONResponse({"ok": False, "requires_profile": True, "asset_id": asset_id}, status_code=409)
                return RedirectResponse(f"/g/{slug}?favourite_setup=1#photo-{asset_id}", status_code=303)
            existing = db.scalar(select(CustomerFavourite).where(
                CustomerFavourite.session_id == favourite_session.id, CustomerFavourite.asset_id == asset_id
            ))
            if existing:
                db.delete(existing); is_active = False
            else:
                db.add(CustomerFavourite(session_id=favourite_session.id, asset_id=asset_id)); is_active = True
            db.commit()
            favourites = _persistent_favourite_ids(db, favourite_session)
            request.session[_favourites_key(gallery.id)] = favourites
        if request.headers.get("X-Requested-With") == "PirouetteFavourite":
            return JSONResponse({"ok": True, "active": is_active, "asset_id": asset_id, "count": len(favourites)})
        if return_to.startswith(f"/g/{slug}/favourites"):
            return RedirectResponse(f"/g/{slug}/favourites", status_code=303)
        # Preserve the exact gallery view as a no-JavaScript fallback. In
        # particular, keep media=photos and folder=... so a favourite click
        # never throws the customer back to the event root.
        if return_to.startswith(f"/g/{slug}") and not return_to.startswith(f"/g/{slug}/basket"):
            separator = "&" if "?" in return_to else "?"
            return RedirectResponse(f"{return_to}{separator}favourite={quote(asset_id)}#photo-{asset_id}", status_code=303)
        return RedirectResponse(f"/g/{slug}?media=photos#photo-{asset_id}", status_code=303)

    @router.post("/g/{slug}/basket/add", include_in_schema=False)
    async def add_to_basket(
        request: Request,
        slug: str,
        asset_id: str = Form(...),
        return_to: str = Form(""),
    ):
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            asset = (
                session.get(GalleryAsset, asset_id)
                if gallery
                else None
            )

            if (
                gallery
                and request.session.get(
                    f"protected_favourites_{gallery.id}"
                )
            ):
                protected_session = (
                    _persistent_favourite_session(
                        session,
                        request,
                        gallery.id,
                    )
                )

                entitled = bool(
                    protected_session
                    and session.scalar(
                        select(CustomerFavourite).where(
                            CustomerFavourite.session_id
                            == protected_session.id,
                            CustomerFavourite.asset_id
                            == asset_id,
                        )
                    )
                )

                if not entitled:
                    return RedirectResponse(
                        f"/g/{slug}/favourites?error="
                        + quote(
                            "That photograph is not in this "
                            "private favourites folder."
                        ),
                        status_code=303,
                    )

            if (
                not _gallery_available(gallery)
                or not asset
                or asset.gallery_id != gallery.id
                or not _unlocked(request, gallery)
            ):
                return RedirectResponse(
                    f"/g/{slug}",
                    status_code=303,
                )

            basket = _basket(request, gallery.id)

            # Pirouette parity:
            # add the photograph first and let the customer choose
            # its product/size in the basket afterwards.
            key = f"{asset_id}:photo"

            existing = next(
                (
                    item
                    for item in basket
                    if str(item.get("asset_id") or "")
                    == str(asset_id)
                    and item.get("product_type")
                    not in {
                        "favourites_extension",
                        "favourites_extension_70",
                    }
                ),
                None,
            )

            if existing is None:
                basket.append(
                    {
                        "key": key,
                        "asset_id": asset_id,
                        "asset_source_ref": asset.source_ref,
                        "filename": asset.filename,
                        "folder_path": asset.folder_path,
                        "product_code": "",
                        "product_type": "",
                        "quantity": 1,
                    }
                )

            request.session[
                _basket_key(gallery.id)
            ] = basket

            # Pirouette parity:
            # add the photograph and keep the customer browsing.
            if (
                return_to.startswith(f"/g/{slug}")
                and not return_to.startswith(
                    f"/g/{slug}/basket"
                )
            ):
                return RedirectResponse(
                    return_to,
                    status_code=303,
                )

            return RedirectResponse(
                f"/g/{slug}?media=photos",
                status_code=303,
            )

    @router.post("/g/{slug}/basket/product", include_in_schema=False)
    async def choose_basket_product(
        request: Request,
        slug: str,
        key: str = Form(...),
        product_code: str = Form(...),
    ):
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            event = (
                session.get(Event, gallery.event_id)
                if gallery
                else None
            )
            catalogue = (
                event_catalogue(event)
                if event
                else {}
            )

        if (
            not _gallery_available(gallery)
            or not _unlocked(request, gallery)
        ):
            return RedirectResponse(
                f"/g/{slug}",
                status_code=303,
            )

        product = catalogue.get(product_code)

        if not product or product.get("sold_out"):
            return RedirectResponse(
                f"/g/{slug}/basket?error="
                + quote(
                    "Please choose a valid available product."
                )
                + "#basket-items",
                status_code=303,
            )

        basket = _basket(request, gallery.id)
        changed = False

        for item in basket:
            if item.get("key") != key:
                continue

            if item.get("product_type") in {
                "favourites_extension",
                "favourites_extension_70",
            }:
                continue

            item["product_code"] = str(product_code)
            item["product_type"] = str(
                product.get("semantic_type")
                or "print"
            )

            if item["product_type"] != "print":
                item["quantity"] = 1
            else:
                item["quantity"] = max(
                    1,
                    int(item.get("quantity") or 1),
                )

            changed = True
            break

        if changed:
            request.session[
                _basket_key(gallery.id)
            ] = basket

        return RedirectResponse(
            f"/g/{slug}/basket#basket-items",
            status_code=303,
        )

    @router.post("/g/{slug}/basket/quantity", include_in_schema=False)
    async def update_basket_quantity(
        request: Request,
        slug: str,
        key: str = Form(...),
        quantity: int = Form(...),
    ):
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            event = (
                session.get(Event, gallery.event_id)
                if gallery
                else None
            )
            catalogue = event_catalogue(event) if event else {}

        if (
            not _gallery_available(gallery)
            or not _unlocked(request, gallery)
        ):
            return RedirectResponse(
                f"/g/{slug}",
                status_code=303,
            )

        basket = _basket(request, gallery.id)

        for item in basket:
            if item.get("key") != key:
                continue

            code = str(item.get("product_code") or "")
            product = catalogue.get(code)

            if not product:
                return RedirectResponse(
                    f"/g/{slug}/basket?error="
                    + quote(
                        "Please choose a product before changing quantity."
                    )
                    + "#basket-items",
                    status_code=303,
                )

            semantic_type = str(
                product.get("semantic_type") or "print"
            )

            if semantic_type != "print":
                item["quantity"] = 1
            else:
                item["quantity"] = max(
                    1,
                    min(50, int(quantity)),
                )

            request.session[
                _basket_key(gallery.id)
            ] = basket

            break

        return RedirectResponse(
            f"/g/{slug}/basket#basket-items",
            status_code=303,
        )

    @router.post("/g/{slug}/basket/duplicate", include_in_schema=False)
    async def duplicate_basket_product(
        request: Request,
        slug: str,
        key: str = Form(...),
    ):
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)

        if (
            not _gallery_available(gallery)
            or not _unlocked(request, gallery)
        ):
            return RedirectResponse(
                f"/g/{slug}",
                status_code=303,
            )

        basket = _basket(request, gallery.id)
        source = next(
            (item for item in basket if item.get("key") == key),
            None,
        )

        if (
            source
            and source.get("asset_id")
            and source.get("product_type")
            not in {
                "favourites_extension",
                "favourites_extension_70",
            }
        ):
            duplicate = dict(source)
            duplicate["key"] = (
                f"{source['asset_id']}:photo:"
                f"{secrets.token_hex(4)}"
            )
            duplicate["product_code"] = ""
            duplicate["product_type"] = ""
            duplicate["quantity"] = 1
            basket.append(duplicate)
            request.session[_basket_key(gallery.id)] = basket

        return RedirectResponse(
            f"/g/{slug}/basket#basket-items",
            status_code=303,
        )

    @router.get("/g/{slug}/basket", response_class=HTMLResponse, include_in_schema=False)
    async def basket_page(request: Request, slug: str):
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            event = (
                session.get(Event, gallery.event_id)
                if gallery
                else None
            )
            basket = (
                _basket(request, gallery.id)
                if gallery
                else []
            )
            catalogue = event_catalogue(event) if event else {}

            asset_ids = [
                str(item.get("asset_id"))
                for item in basket
                if item.get("asset_id")
            ]

            asset_map = (
                {
                    asset.id: asset
                    for asset in session.scalars(
                        select(GalleryAsset).where(
                            GalleryAsset.id.in_(asset_ids)
                        )
                    )
                }
                if asset_ids
                else {}
            )

        if (
            not _gallery_available(gallery)
            or not _unlocked(request, gallery)
        ):
            return RedirectResponse(
                f"/g/{slug}",
                status_code=303,
            )

        rows = []

        subtotal, product_totals = basket_pricing(
            basket,
            catalogue,
        )

        for item in basket:
            code = str(
                item.get("product_code")
                or item.get("product_type")
                or ""
            )

            product = catalogue.get(code)

            if product:
                line = (
                    int(product.get("price_pence") or 0)
                    * max(
                        1,
                        int(item.get("quantity", 1)),
                    )
                )
            else:
                line = 0

            rows.append(
                item
                | {
                    "product": product,
                    "line_total_pence": line,
                    "asset": asset_map.get(
                        str(item.get("asset_id"))
                    ),
                }
            )

        profile = request.session.get(
            f"customer_profile_{gallery.id}",
            {},
        )

        preference_key = f"customer_checkout_preferences_{gallery.id}"
        saved_preferences = request.session.get(preference_key, {})
        if not isinstance(saved_preferences, dict):
            saved_preferences = {}
        default_print_fulfilment = (
            "event_collection"
            if event.status == "live"
            and event.event_collection_available
            else "home_delivery"
        )
        default_digital_fulfilment = (
            "event_collection"
            if event.status == "live"
            else "secure_email_delivery"
        )
        checkout_preferences = {
            "print_fulfilment": str(
                saved_preferences.get("print_fulfilment")
                or default_print_fulfilment
            ),
            "digital_fulfilment": str(
                saved_preferences.get("digital_fulfilment")
                or default_digital_fulfilment
            ),
        }

        has_print = any(
            item.get("product_type") == "print"
            for item in basket
        )

        # Sophie’s Rewards:
        # Cloud may DISPLAY an authoritative Offline snapshot but never
        # owns or redeems the loyalty ledger.

        qualifying_items = []

        for item in basket:

            code = str(
                item.get("product_code")
                or item.get("product_type")
                or ""
            )

            product = catalogue.get(code)

            if not product:
                continue

            semantic_type = str(
                product.get("semantic_type")
                or item.get("product_type")
                or ""
            )

            if semantic_type in {
                "favourites_extension",
                "favourites_extension_70",
            }:
                continue

            if item.get("asset_id"):
                qualifying_items.append(item)

        rewards_qualifying_pence = (
            basket_pricing(
                qualifying_items,
                catalogue,
            )[0]
            if qualifying_items
            else 0
        )

        rewards_earned_points = max(
            0,
            int(rewards_qualifying_pence or 0) // 100,
        )

        rewards_email = ""
        rewards_points = 0

        # Never expose a balance merely because somebody typed an email
        # address into checkout. Existing balance is shown only to a
        # verified customer identity.

        with request.app.state.session_factory() as rewards_session:

            verified_rewards_customer = verified_customer_profile(
                rewards_session,
                request,
            )

            if isinstance(
                verified_rewards_customer,
                dict,
            ):

                rewards_email = _normalise_email(
                    str(
                        verified_rewards_customer.get(
                            "email"
                        )
                        or ""
                    )
                )

            elif verified_rewards_customer is not None:

                rewards_email = _normalise_email(
                    str(
                        getattr(
                            verified_rewards_customer,
                            "email",
                            "",
                        )
                        or ""
                    )
                )

            if rewards_email:

                rewards_snapshot = rewards_session.get(
                    CustomerRewardSnapshot,
                    rewards_email,
                )

                if rewards_snapshot is not None:

                    rewards_points = max(
                        0,
                        int(
                            rewards_snapshot.points
                            or 0
                        ),
                    )

        brand = get_event_brand(event)

        rewards = {
            "verified": bool(rewards_email),
            "points": rewards_points,
            "earned_points": rewards_earned_points,
            "projected_points": (
                rewards_points
                + rewards_earned_points
            ),
            "qualifying_pence":
                rewards_qualifying_pence,
        }

        return templates.TemplateResponse(
            request=request,
            name=(
                "customer_basket_panel.html"
                if request.query_params.get("panel") == "1"
                else "customer_basket.html"
            ),
            context={
                "gallery": gallery,
                "event": event,
                "brand": brand,
                "rows": rows,
                "subtotal_pence": subtotal,
                "product_catalogue": catalogue,
                "profile": profile,
                "has_print": has_print,
                "checkout_preferences": checkout_preferences,
                "rewards": rewards,
                "csrf_token": _csrf(request),
                "error": request.query_params.get(
                    "error",
                    "",
                ),
                "panel": (
                    request.query_params.get("panel") == "1"
                ),
            },
        )

    @router.post("/g/{slug}/basket/remove", include_in_schema=False)
    async def remove_from_basket(request: Request, slug: str, key: str = Form(...)):
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
        if gallery:
            current = _basket(request, gallery.id)
            removed_extension = any(item.get("key") == key and item.get("product_type") in {"favourites_extension", "favourites_extension_70"} for item in current)
            request.session[_basket_key(gallery.id)] = [item for item in current if item.get("key") != key]
            if removed_extension:
                with request.app.state.session_factory() as db:
                    favourite_session = _persistent_favourite_session(db, request, gallery.id)
                    if favourite_session and not favourite_session.extension_paid:
                        favourite_session.extension_requested = False
                        favourite_session.retention_choice = "temporary_link"
                        event = db.get(Event, gallery.event_id)
                        favourite_session.expires_at = None if event and event.status == "live" else datetime.now(timezone.utc) + timedelta(days=5)
                        db.commit()
        return RedirectResponse(f"/g/{slug}/basket", status_code=303)

    @router.get("/g/{slug}/checkout", response_class=HTMLResponse, include_in_schema=False)
    async def checkout_page(request: Request, slug: str, error: str = ""):
        suffix = ("?error=" + quote(error)) if error else ""
        return RedirectResponse(f"/g/{slug}/basket{suffix}#checkout", status_code=303)

    @router.post("/g/{slug}/checkout", include_in_schema=False)
    async def checkout_submit(
        request: Request, slug: str, customer_name: str = Form(...), customer_email: str = Form(...),
        customer_phone: str = Form(""), customer_at_event: str = Form("false"), payment_method: str = Form("sumup"),
        print_fulfilment: str = Form("home_delivery"), digital_fulfilment: str = Form("secure_email_delivery"),
        address_line_1: str = Form(""), address_line_2: str = Form(""),
        town: str = Form(""), county: str = Form(""), postcode: str = Form(""), country: str = Form("United Kingdom"),
        csrf_token: str = Form(...),
    ):
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(f"/g/{slug}/basket?error=" + quote("The checkout page expired. Please try again.") + "#checkout", status_code=303)

        # Pirouette checkout customer persistence
        #
        # Preserve the customer's entered contact information before any
        # payment, fulfilment or address validation can redirect them back
        # to the basket. This is especially important when returning from
        # SumUp or changing to Pay at the Stand.
        submitted_customer_profile = {
            "name": str(customer_name or "").strip()[:100],
            "email": _normalise_email(customer_email),
            "phone": str(customer_phone or "").strip()[:40],
        }

        payment_method = {
            "service_desk_cash": "service_desk",
            "service_desk_card": "service_desk",
            "sumup_online": "sumup",
        }.get(payment_method, payment_method)

        if digital_fulfilment not in {
            "event_collection",
            "secure_email_delivery",
        }:
            digital_fulfilment = "secure_email_delivery"

        if print_fulfilment not in {
            "event_collection",
            "home_delivery",
        }:
            print_fulfilment = "home_delivery"

        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            event = session.get(Event, gallery.event_id) if gallery else None
            if not _gallery_available(gallery) or event is None or not _unlocked(request, gallery):
                return RedirectResponse(f"/g/{slug}", status_code=303)
            preference_key = f"customer_checkout_preferences_{gallery.id}"
            if (
                print_fulfilment == "event_collection"
                and (
                    event.status != "live"
                    or not event.event_collection_available
                )
            ):
                print_fulfilment = "home_delivery"
            if (
                digital_fulfilment == "event_collection"
                and (
                    event.status != "live"
                    or not event.event_collection_available
                )
            ):
                digital_fulfilment = "secure_email_delivery"
            request.session[preference_key] = {
                "print_fulfilment": print_fulfilment,
                "digital_fulfilment": digital_fulfilment,
            }

            basket = _basket(request, gallery.id)
            catalogue = event_catalogue(event)
            if not basket:
                return RedirectResponse(
                    f"/g/{slug}/basket",
                    status_code=303,
                )

            missing_product = any(
                item.get("asset_id")
                and not str(
                    item.get("product_code")
                    or ""
                ).strip()
                for item in basket
                if item.get("product_type")
                not in {
                    "favourites_extension",
                    "favourites_extension_70",
                }
            )

            if missing_product:
                return RedirectResponse(
                    f"/g/{slug}/basket?error="
                    + quote(
                        "Please choose a product for each "
                        "photograph before continuing."
                    )
                    + "#basket-items",
                    status_code=303,
                )
            if request.session.get(f"protected_favourites_{gallery.id}"):
                protected_session = _persistent_favourite_session(session, request, gallery.id)
                entitled_ids = set(_persistent_favourite_ids(session, protected_session))
                forged = [
                    str(item.get("asset_id")) for item in basket
                    if item.get("asset_id") and str(item.get("asset_id")) not in entitled_ids
                ]
                if forged:
                    return RedirectResponse(
                        f"/g/{slug}/basket?error=" + quote("Your basket contained a photograph that is not in this private favourites folder. It has not been checked out.") + "#checkout",
                        status_code=303,
                    )
            # Selecting event collection is itself confirmation that the
            # customer is at this live event. This must work even when the
            # basket is rendered inside the gallery drawer where inline
            # JavaScript may not execute.
            at_event = (
                event.status == "live"
                and (
                    customer_at_event == "true"
                    or print_fulfilment == "event_collection"
                    or digital_fulfilment == "event_collection"
                    or payment_method == "service_desk"
                )
            )
            request.session[
                f"customer_profile_{gallery.id}"
            ] = submitted_customer_profile

            grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
            total_pence, product_totals = basket_pricing(basket, catalogue)
            delivery_charge = 0
            for item in basket:
                product_code = str(item.get("product_code") or item.get("product_type") or "")
                product = catalogue.get(product_code)
                if not product or product.get("sold_out"):
                    return RedirectResponse(f"/g/{slug}/basket?error=" + quote("A product in your basket is no longer available. Please remove it and choose again.") + "#checkout", status_code=303)
                product_type = str(product.get("semantic_type") or item.get("product_type") or "print")
                item["product_code"] = product_code
                item["product_type"] = product_type
                wants_home = product_type == "print" and print_fulfilment != "event_collection"
                options = checkout_options(event, customer_at_event=at_event, product_type=product_type, wants_home_delivery=wants_home)
                if payment_method not in options["payment_methods"]:
                    return RedirectResponse(f"/g/{slug}/basket?error=" + quote("That payment option is no longer available.") + "#checkout", status_code=303)
                if options["address_required"] and not all([address_line_1.strip(), town.strip(), postcode.strip()]):
                    return RedirectResponse(f"/g/{slug}/basket?error=" + quote("Please enter the delivery address for your print order.") + "#checkout", status_code=303)

                fulfilment_method = str(options["fulfilment_method"])

                if product_type in {"low_res", "high_res"}:
                    if (
                        digital_fulfilment == "event_collection"
                        and event.status == "live"
                        and event.event_collection_available
                    ):
                        fulfilment_method = "event_collection"
                    else:
                        fulfilment_method = "secure_email_delivery"

                grouped[(product_type, fulfilment_method)].append(item)
                delivery_charge = max(delivery_charge, int(options["delivery_charge_pence"]))
            checkout_ref = _order_ref()
            order_ids = []
            for index, ((product_type, fulfilment), items) in enumerate(grouped.items(), start=1):
                group_ref = checkout_ref if len(grouped) == 1 else f"{checkout_ref}-{index}"
                order = CloudOrder(
                    source_ref=f"cloud:{group_ref}", event_id=event.id, order_reference=group_ref,
                    customer_name=customer_name.strip(), customer_email=customer_email.strip().lower(), customer_phone=customer_phone.strip(),
                    customer_at_event=at_event, product_type=product_type, payment_method=payment_method,
                    payment_status="awaiting_payment", fulfilment_method=fulfilment,
                    order_items_json=json.dumps([{"asset_source_ref": i.get("asset_source_ref", ""), "filename": i["filename"], "folder_path": i.get("folder_path", ""), "quantity": i.get("quantity", 1), "unit_price_pence": int(catalogue.get(str(i.get("product_code") or i.get("product_type")), {}).get("price_pence") or 0), "product_code": str(i.get("product_code") or i.get("product_type") or ""), "product_name": str(catalogue.get(str(i.get("product_code") or i.get("product_type")), {}).get("name") or i.get("product_code") or product_type), "product_type": product_type, "favourite_token": i.get("favourite_token", "")} for i in items]),
                    address_line_1=address_line_1.strip() if fulfilment == "home_delivery" else "", address_line_2=address_line_2.strip() if fulfilment == "home_delivery" else "",
                    town=town.strip() if fulfilment == "home_delivery" else "", county=county.strip() if fulfilment == "home_delivery" else "",
                    postcode=postcode.strip() if fulfilment == "home_delivery" else "", country=(country.strip() or "United Kingdom") if fulfilment == "home_delivery" else "United Kingdom",
                    delivery_charge_pence=delivery_charge if fulfilment == "home_delivery" else 0,
                    status="checkout_pending" if payment_method == "sumup" else "awaiting_staff_payment",
                    payment_amount_pence=(group_subtotal := basket_pricing(items, catalogue)[0]) + (delivery_charge if fulfilment == "home_delivery" else 0),
                    staff_note=f"Cloud checkout {checkout_ref}. Basket subtotal £{total_pence/100:.2f}; delivery £{delivery_charge/100:.2f}.",
                )
                session.add(order); session.flush(); order_ids.append(order.id)
            session.commit()
            orders = list(session.scalars(select(CloudOrder).where(CloudOrder.id.in_(order_ids))))
            payment_url = ""
            if payment_method == "sumup":
                print("SUMUP_TRACE entering_sumup", flush=True)
                try:
                    base = request.app.state.settings.public_base_url.rstrip("/")
                    checkout = attach_checkout(
                        session,
                        request.app.state.settings,
                        orders,
                        checkout_reference=checkout_ref,
                        brand_name=get_event_brand(event)["display_name"],
                        redirect_url=f"{base}/g/{slug}/orders/{checkout_ref}/payment-return",
                        return_url=f"{base}/api/payments/sumup/webhook",
                    )
                    payment_url = checkout.checkout_url
                    print(
                        "SUMUP_TRACE checkout_created",
                        {
                            "checkout_id": checkout.checkout_id,
                            "url_present": bool(payment_url),
                        },
                        flush=True,
                    )
                    print(
                        "SUMUP_TRACE checkout_created",
                        {
                            "checkout_id": checkout.checkout_id,
                            "url_present": bool(payment_url),
                        },
                        flush=True,
                    )
                    # attach_checkout historically labelled the row awaiting_payment.
                    # Keep online payment attempts explicitly in draft state until SumUp
                    # verifies payment so Offline never mistakes a payment page visit for
                    # a completed checkout.
                    for order in orders:
                        order.status = "checkout_pending"
                    session.commit()
                except SumUpError as exc:
                    # A failed SumUp hand-off is not a submitted customer order. Keep
                    # it as a private checkout draft, preserve the basket, and return
                    # the customer to the checkout with an actionable explanation.
                    for order in orders:
                        order.payment_status = "payment_setup_failed"
                        order.status = "checkout_pending"
                        order.sumup_status = "SETUP_FAILED"
                        order.fulfilment_error = str(exc)[:1000]
                    session.commit()
                    request.session[f"customer_order_{checkout_ref}"] = order_ids
                    message = customer_safe_error(exc)
                    return RedirectResponse(
                        f"/g/{slug}/basket?error=" + quote(message) + "#checkout",
                        status_code=303,
                    )
        # Do not empty the basket merely because the customer opened SumUp.
        # Keeping it intact lets them safely return and choose another payment method
        # without losing their selections. Pay-at-the-Stand is a committed checkout, so
        # that path can clear the basket immediately.
        if payment_method != "sumup":
            request.session[_basket_key(gallery.id)] = []
        request.session[f"customer_order_{checkout_ref}"] = order_ids
        if payment_method == "sumup" and payment_url:
            print("SUMUP_TRACE redirecting_to_sumup", flush=True)
            print("SUMUP_TRACE redirecting_to_sumup", flush=True)
            return RedirectResponse(payment_url, status_code=303)
        return RedirectResponse(f"/g/{slug}/orders/{checkout_ref}", status_code=303)

    @router.get("/g/{slug}/orders/{checkout_ref}", response_class=HTMLResponse, include_in_schema=False)
    async def order_confirmation(request: Request, slug: str, checkout_ref: str):
        ids = request.session.get(f"customer_order_{checkout_ref}", [])
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            event = (
                session.get(Event, gallery.event_id)
                if gallery
                else None
            )
            orders = list(session.scalars(select(CloudOrder).where(CloudOrder.id.in_(ids)))) if ids else []
            asset_refs = []
            for order in orders:
                try:
                    asset_refs.extend(str(item.get("asset_source_ref") or "") for item in json.loads(order.order_items_json or "[]") if item.get("asset_source_ref"))
                except Exception:
                    pass
            confirmation_assets = list(session.scalars(select(GalleryAsset).where(GalleryAsset.source_ref.in_(asset_refs)))) if asset_refs else []
            _activate_paid_favourites_extensions(session, orders)
        if not gallery or not orders:
            return RedirectResponse(f"/g/{slug}", status_code=303)
        total_delivery = sum(order.delivery_charge_pence for order in orders)
        payment_url = next((order.sumup_checkout_url for order in orders if order.sumup_checkout_url), "")
        payment_status = "paid" if all(order.payment_status == "paid" for order in orders) else orders[0].payment_status
        token = secrets.token_urlsafe(24)
        request.session[f"payment_retry_csrf_{checkout_ref}"] = token
        brand = get_event_brand(event)

        return templates.TemplateResponse(request=request, name="customer_order_confirmation.html", context={
            "gallery": gallery, "event": event, "brand": brand,
            "orders": orders, "checkout_ref": checkout_ref,
            "total_delivery_pence": total_delivery, "payment_url": payment_url,
            "payment_status": payment_status, "payment_retry_csrf": token, "confirmation_assets": confirmation_assets,
        })

    @router.get("/g/{slug}/orders/{checkout_ref}/payment-return", include_in_schema=False)
    async def payment_return(request: Request, slug: str, checkout_ref: str):
        ids = request.session.get(f"customer_order_{checkout_ref}", [])
        payment_confirmed = False
        gallery_id = None
        if ids:
            with request.app.state.session_factory() as session:
                gallery = get_gallery_by_slug(session, slug)
                gallery_id = gallery.id if gallery else None
                orders = list(session.scalars(select(CloudOrder).where(CloudOrder.id.in_(ids))))
                checkout_id = next((order.sumup_checkout_id for order in orders if order.sumup_checkout_id), "")
                if checkout_id:
                    try:
                        verified = verify_and_apply_checkout(session, request.app.state.settings, checkout_id)
                        payment_confirmed = bool(verified and all(order.payment_status == "paid" for order in verified))
                    except SumUpError:
                        pass
        if payment_confirmed and gallery_id:
            # Only a payment positively verified as PAID by SumUp may
            # complete the customer journey and clear the basket.
            request.session[_basket_key(gallery_id)] = []
            return RedirectResponse(
                f"/g/{slug}/orders/{checkout_ref}",
                status_code=303,
            )

        # Closing/cancelling SumUp, or returning while the checkout is still
        # pending/failed, is NOT a completed order. Preserve the basket and
        # return the customer to checkout so they can retry or choose another
        # payment method.
        return RedirectResponse(
            f"/g/{slug}/basket?error="
            + quote("Payment was not completed. Your basket has been kept so you can try again.")
            + "#checkout",
            status_code=303,
        )

    @router.post("/g/{slug}/orders/{checkout_ref}/retry-payment", include_in_schema=False)
    async def retry_payment(request: Request, slug: str, checkout_ref: str, csrf_token: str = Form(...)):
        expected = str(request.session.get(f"payment_retry_csrf_{checkout_ref}", ""))
        if not expected or not secrets.compare_digest(expected, csrf_token):
            return RedirectResponse(f"/g/{slug}/orders/{checkout_ref}", status_code=303)
        ids = request.session.get(f"customer_order_{checkout_ref}", [])
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            orders = list(session.scalars(select(CloudOrder).where(CloudOrder.id.in_(ids)))) if ids else []
            if not gallery or not orders or any(order.payment_status == "paid" for order in orders):
                return RedirectResponse(f"/g/{slug}/orders/{checkout_ref}", status_code=303)
            try:
                base = request.app.state.settings.public_base_url.rstrip("/")
                retry_event = session.get(Event, orders[0].event_id)
                checkout = attach_checkout(
                    session, request.app.state.settings, orders, checkout_reference=checkout_ref,
                    brand_name=get_event_brand(retry_event)["display_name"],
                    redirect_url=f"{base}/g/{slug}/orders/{checkout_ref}/payment-return",
                    return_url=f"{base}/api/payments/sumup/webhook",
                )
            except SumUpError as exc:
                for order in orders:
                    order.fulfilment_error = str(exc)[:1000]
                session.commit()
                return RedirectResponse(f"/g/{slug}/orders/{checkout_ref}", status_code=303)
        return RedirectResponse(checkout.checkout_url, status_code=303)

    return router
