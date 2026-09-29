from __future__ import annotations

import hmac
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select

from app.auth.service import new_csrf_token
from app.checkout.catalogue import event_catalogue
from app.customer_ownership import verified_customer_profile
from app.customer_activity import record_customer_activity
from app.db.models import CustomerFavourite, CustomerFavouriteSession, Event, GalleryAsset, GalleryFolder
from app.branding import get_event_brand
from app.db.session import database_is_ready, database_schema_is_ready
from app.storage import presigned_get_url
from app.gallery.service import (
    GALLERY_STATUSES,
    GALLERY_VISIBILITIES,
    create_gallery,
    get_gallery,
    get_gallery_by_slug,
    list_galleries,
    random_access_code,
    update_gallery,
    verify_access_code,
)


def _require_staff(request: Request):
    if not request.session.get("staff_authenticated"):
        return RedirectResponse("/staff/login", status_code=status.HTTP_303_SEE_OTHER)
    return None


def _csrf(request: Request) -> str:
    token = new_csrf_token()
    request.session["gallery_csrf_token"] = token
    return token


def _valid_csrf(request: Request, supplied: str) -> bool:
    expected = str(request.session.get("gallery_csrf_token", ""))
    return bool(expected and hmac.compare_digest(expected, supplied))


def _database_unavailable(request: Request, templates: Jinja2Templates):
    engine = getattr(request.app.state, "engine", None)
    connection_ready = bool(engine and database_is_ready(engine))
    schema_ready = bool(engine and connection_ready and database_schema_is_ready(engine))
    if getattr(request.app.state, "session_factory", None) is not None and schema_ready:
        return None
    return templates.TemplateResponse(
        request=request,
        name="database_unavailable.html",
        context={
            "database_error": getattr(request.app.state, "database_error", None),
            "connection_ready": connection_ready,
            "schema_ready": schema_ready,
            "staff_email": request.session.get("staff_email", ""),
            "version": request.app.state.settings.version,
        },
        status_code=503,
    )


def _customer_device(request: Request) -> str:
    ua=str(request.headers.get("user-agent") or "").lower()
    if any(token in ua for token in ("iphone","android","mobile","windows phone")):
        return "mobile"
    if any(token in ua for token in ("ipad","tablet")):
        return "tablet"
    return "desktop"

def _parse_date(value: str) -> date | None:
    return date.fromisoformat(value) if value.strip() else None


def build_gallery_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(tags=["galleries"])

    @router.get("/staff/galleries", response_class=HTMLResponse, include_in_schema=False)
    async def galleries_index(request: Request, status_filter: str = ""):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            galleries = list_galleries(session, status=status_filter)
        return templates.TemplateResponse(request=request, name="galleries_list.html", context={
            "galleries": galleries,
            "statuses": GALLERY_STATUSES,
            "status_filter": status_filter,
            "staff_email": request.session.get("staff_email", ""),
        })

    @router.get("/staff/galleries/new", response_class=HTMLResponse, include_in_schema=False)
    async def gallery_new(request: Request, event_id: str = "", error: str = ""):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            events = list(session.scalars(select(Event).where(Event.status != "archived").order_by(Event.start_date.desc())))
        return templates.TemplateResponse(request=request, name="gallery_form.html", context={
            "gallery": None, "events": events, "selected_event_id": event_id,
            "statuses": GALLERY_STATUSES, "visibilities": GALLERY_VISIBILITIES,
            "csrf_token": _csrf(request), "error": error, "form_action": "/staff/galleries/new",
            "suggested_code": random_access_code(), "staff_email": request.session.get("staff_email", ""),
        })

    @router.post("/staff/galleries/new", include_in_schema=False)
    async def gallery_create(request: Request, event_id: str = Form(...), name: str = Form(...), slug: str = Form(""),
                             gallery_status: str = Form("draft"), visibility: str = Form("private"),
                             access_code: str = Form(""), expires_at: str = Form(""), description: str = Form(""),
                             csrf_token: str = Form(...)):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse("/staff/galleries/new?error=" + quote("The form expired. Please try again."), status_code=303)
        clean_name = name.strip()
        if not clean_name or gallery_status not in GALLERY_STATUSES or visibility not in GALLERY_VISIBILITIES:
            return RedirectResponse("/staff/galleries/new?error=" + quote("Complete the required gallery fields."), status_code=303)
        with request.app.state.session_factory() as session:
            if session.get(Event, event_id) is None:
                return RedirectResponse("/staff/galleries/new?error=" + quote("Choose a valid event."), status_code=303)
            gallery = create_gallery(session, event_id=event_id, name=clean_name, slug=slug,
                                     status=gallery_status, visibility=visibility, access_code=access_code.strip(),
                                     expires_at=_parse_date(expires_at), description=description.strip())
        return RedirectResponse(f"/staff/galleries/{gallery.id}", status_code=303)

    @router.get("/staff/galleries/{gallery_id}", response_class=HTMLResponse, include_in_schema=False)
    async def gallery_detail(request: Request, gallery_id: str):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            gallery = get_gallery(session, gallery_id)
            event = session.get(Event, gallery.event_id) if gallery else None
            assets = list(session.scalars(select(GalleryAsset).where(GalleryAsset.gallery_id == gallery.id, GalleryAsset.status == "ready").order_by(GalleryAsset.created_at.desc()))) if gallery else []
        if gallery is None:
            return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
        return templates.TemplateResponse(request=request, name="gallery_detail.html", context={
            "gallery": gallery, "event": event, "csrf_token": _csrf(request),
            "staff_email": request.session.get("staff_email", ""),
        })

    @router.get("/staff/galleries/{gallery_id}/edit", response_class=HTMLResponse, include_in_schema=False)
    async def gallery_edit(request: Request, gallery_id: str, error: str = ""):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            gallery = get_gallery(session, gallery_id)
            events = list(session.scalars(select(Event).where(Event.status != "archived").order_by(Event.start_date.desc())))
        if gallery is None:
            return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
        return templates.TemplateResponse(request=request, name="gallery_form.html", context={
            "gallery": gallery, "events": events, "selected_event_id": gallery.event_id,
            "statuses": GALLERY_STATUSES, "visibilities": GALLERY_VISIBILITIES,
            "csrf_token": _csrf(request), "error": error, "form_action": f"/staff/galleries/{gallery_id}/edit",
            "suggested_code": "", "staff_email": request.session.get("staff_email", ""),
        })

    @router.post("/staff/galleries/{gallery_id}/edit", include_in_schema=False)
    async def gallery_update(request: Request, gallery_id: str, event_id: str = Form(...), name: str = Form(...),
                             gallery_status: str = Form("draft"), visibility: str = Form("private"),
                             access_code: str = Form(""), expires_at: str = Form(""), description: str = Form(""),
                             csrf_token: str = Form(...)):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(f"/staff/galleries/{gallery_id}/edit?error=" + quote("The form expired. Please try again."), status_code=303)
        with request.app.state.session_factory() as session:
            gallery = get_gallery(session, gallery_id)
            if gallery is None:
                return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
            update_gallery(session, gallery, event_id=event_id, name=name.strip(), status=gallery_status,
                           visibility=visibility, access_code=access_code.strip(), expires_at=_parse_date(expires_at),
                           description=description.strip())
        return RedirectResponse(f"/staff/galleries/{gallery_id}", status_code=303)

    @router.get("/g/{slug}", response_class=HTMLResponse, include_in_schema=False)
    async def public_gallery(
        request: Request,
        slug: str,
        error: str = "",
        media: str = Query("", pattern="^(|photos|videos)$"),
        folder: str = Query("", max_length=800),
        q: str = Query("", max_length=200),
        page: int = Query(1, ge=1),
    ):
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return templates.TemplateResponse(request=request, name="gallery_unavailable.html", context={}, status_code=503)
        folder = "/".join(part for part in folder.replace("\\", "/").strip("/").split("/") if part not in ("", ".", ".."))
        query = " ".join(q.strip().split())
        query_lower = query.casefold()

        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            if gallery is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )
            event = session.get(Event, gallery.event_id)
            all_folders = list(session.scalars(
                select(GalleryFolder).where(
                    GalleryFolder.gallery_id == gallery.id,
                    GalleryFolder.active.is_(True),
                ).order_by(GalleryFolder.sort_order, GalleryFolder.name)
            )) if gallery else []
            media_folders = [row for row in all_folders if row.media_kind == media] if media else []

            if query and media == "photos":
                matching_folders = [
                    row
                    for row in media_folders
                    if query_lower in (
                        f"{row.name or ''} {row.path or ''}"
                    ).casefold()
                ]

                # Return the most useful matching folder rather than every
                # ancestor/descendant whose path happens to contain the term.
                matching_paths = {row.path for row in matching_folders}

                child_folders = [
                    row
                    for row in matching_folders
                    if not any(
                        other_path != row.path
                        and row.path.startswith(f"{other_path}/")
                        for other_path in matching_paths
                    )
                ]
            else:
                child_folders = [
                    row
                    for row in media_folders
                    if row.parent_path == folder
                ]

            folder_cover_assets = {}
            if gallery and media == "photos" and child_folders:
                child_paths = [child.path for child in child_folders]

                cover_conditions = []
                for child_path in child_paths:
                    cover_conditions.extend([
                        GalleryAsset.folder_path == child_path,
                        GalleryAsset.folder_path.like(f"{child_path}/%"),
                    ])

                cover_candidates = list(session.scalars(
                    select(GalleryAsset).where(
                        GalleryAsset.gallery_id == gallery.id,
                        GalleryAsset.status == "ready",
                        GalleryAsset.media_kind == "photos",
                        or_(*cover_conditions),
                    ).order_by(GalleryAsset.created_at.desc())
                ))

                # Candidates are newest first, so the first match becomes
                # the cover for that child folder.
                for asset in cover_candidates:
                    for child_path in child_paths:
                        if (
                            asset.folder_path == child_path
                            or asset.folder_path.startswith(f"{child_path}/")
                        ):
                            folder_cover_assets.setdefault(child_path, asset)
                            break

                    if len(folder_cover_assets) == len(child_paths):
                        break
            if query and media == "photos":
                matching_folder_paths = [
                    row.path
                    for row in media_folders
                    if query_lower in (
                        f"{row.name or ''} {row.path or ''}"
                    ).casefold()
                ]

                search_conditions = [
                    GalleryAsset.filename.ilike(f"%{query}%"),
                    GalleryAsset.folder_path.ilike(f"%{query}%"),
                ]

                for matching_path in matching_folder_paths:
                    search_conditions.extend([
                        GalleryAsset.folder_path == matching_path,
                        GalleryAsset.folder_path.like(
                            f"{matching_path}/%"
                        ),
                    ])

                asset_query = (
                    select(GalleryAsset).where(
                        GalleryAsset.gallery_id == gallery.id,
                        GalleryAsset.status == "ready",
                        GalleryAsset.media_kind == "photos",
                        or_(*search_conditions),
                    ).order_by(GalleryAsset.created_at.desc())
                )
            else:
                asset_query = (
                    select(GalleryAsset).where(
                        GalleryAsset.gallery_id == gallery.id,
                        GalleryAsset.status == "ready",
                        GalleryAsset.media_kind == media,
                        GalleryAsset.folder_path == folder,
                    ).order_by(GalleryAsset.created_at.desc())
                )

            gallery_page_size = 48
            has_more_assets = False

            if media == "photos":
                # Fetch one extra record so we know whether another page exists.
                asset_query = asset_query.offset(
                    (page - 1) * gallery_page_size
                ).limit(gallery_page_size + 1)

            assets = list(session.scalars(asset_query)) if gallery and media else []

            if media == "photos" and len(assets) > gallery_page_size:
                has_more_assets = True
                assets = assets[:gallery_page_size]
            all_asset_count = session.scalar(select(func.count(GalleryAsset.id)).where(
                GalleryAsset.gallery_id == gallery.id,
                GalleryAsset.status == "ready",
            )) if gallery else 0
            photo_asset_count = session.scalar(select(func.count(GalleryAsset.id)).where(
                GalleryAsset.gallery_id == gallery.id,
                GalleryAsset.status == "ready",
                GalleryAsset.media_kind == "photos",
            )) if gallery else 0
            video_asset_count = session.scalar(select(func.count(GalleryAsset.id)).where(
                GalleryAsset.gallery_id == gallery.id,
                GalleryAsset.status == "ready",
                GalleryAsset.media_kind == "videos",
            )) if gallery else 0
            favourite_ids = []
            basket_photo_count = 0
            if gallery:
                favourite_token = str(request.session.get(f"customer_favourites_token_{gallery.id}", "") or "")
                favourite_session = session.scalar(select(CustomerFavouriteSession).where(
                    CustomerFavouriteSession.gallery_id == gallery.id,
                    CustomerFavouriteSession.token == favourite_token,
                )) if favourite_token else None
                if favourite_session:
                    favourite_ids = [str(value) for value in session.scalars(select(CustomerFavourite.asset_id).where(
                        CustomerFavourite.session_id == favourite_session.id
                    ))]
                else:
                    favourite_ids = [str(item) for item in request.session.get(f"customer_favourites_{gallery.id}", []) if item]

                basket_items = request.session.get(
                    f"customer_basket_{gallery.id}",
                    [],
                )
                if not isinstance(basket_items, list):
                    basket_items = []

                basket_photo_count = len({
                    str(item.get("asset_id") or "")
                    for item in basket_items
                    if isinstance(item, dict)
                    and str(item.get("asset_id") or "")
                    and item.get("product_type") not in {
                        "favourites_extension",
                        "favourites_extension_70",
                    }
                })
        if gallery is None or gallery.status != "published" or (gallery.expires_at and gallery.expires_at < date.today()):
            return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
        # Never advertise an empty video library. Folder manifests can contain
        # pre-created video folders, but customer navigation appears only when
        # at least one ready video asset genuinely exists in this gallery.
        if media == "videos" and not int(video_asset_count or 0):
            target = f"/g/{slug}?media=photos" if int(photo_asset_count or 0) else f"/g/{slug}"
            return RedirectResponse(target, status_code=303)
        verified_profile = None

        activity_folder_key = (
            f"pirouette_activity_last_folder_{gallery.id}"
        )

        previous_activity_folder = str(
            request.session.get(
                activity_folder_key,
                "",
            )
            or ""
        )

        folder_opened = bool(
            folder
            and folder != previous_activity_folder
        )

        record_customer_activity(
            session,
            request,
            gallery,
            area="gallery",
            folder=folder,
            folder_view=folder_opened,
        )

        request.session[
            activity_folder_key
        ] = folder

        unlocked = True
        breadcrumbs = []
        current = ""
        for part in [part for part in folder.split("/") if part]:
            current = f"{current}/{part}".strip("/")
            breadcrumbs.append({"name": part, "path": current})
        brand = get_event_brand(event)

        return templates.TemplateResponse(request=request, name="public_gallery.html", context={
            "gallery": gallery, "event": event, "brand": brand, "assets": assets,
            "image_assets": assets if media == "photos" else [],
            "video_assets": assets if media == "videos" else [],
            "folders": child_folders, "folder_cover_assets": folder_cover_assets, "current_folder": folder, "breadcrumbs": breadcrumbs,
            "media": media, "total_asset_count": int(all_asset_count or 0),
            "total_folder_count": len(all_folders),
            "photo_storage_available": bool(photo_asset_count or any(row.media_kind == "photos" for row in all_folders)),
            "video_storage_available": bool(video_asset_count),
            "photo_folder_count": sum(1 for row in all_folders if row.media_kind == "photos" and row.parent_path == ""),
            "video_folder_count": sum(1 for row in all_folders if row.media_kind == "videos" and row.parent_path == ""),
            "unlocked": unlocked, "error": error,
            "verified_customer": True,
            "verified_profile": verified_profile,
            "favourite_ids": set(favourite_ids),
            "favourite_count": len(set(favourite_ids)),
            "basket_photo_count": basket_photo_count,
            "profile": request.session.get(f"customer_profile_{gallery.id}", {}),
            "product_catalogue": [p for code,p in event_catalogue(event).items() if code != "favourites_extension" and not p.get("sold_out")],
            "customer_device": _customer_device(request),
            "gallery_page": page,
            "gallery_page_size": gallery_page_size,
            "has_more_assets": has_more_assets,
            "query": query,
        })


    @router.post(
        "/g/{slug}/activity",
        include_in_schema=False,
    )
    async def customer_gallery_activity(
        request: Request,
        slug: str,
    ):
        unavailable = _database_unavailable(
            request,
            templates,
        )

        if unavailable:
            return {
                "ok": False,
                "reason": "database_unavailable",
            }

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        action = str(
            payload.get("action") or "heartbeat"
        ).strip().lower()

        folder_value = str(
            payload.get("folder") or ""
        ).strip()[:800]

        area = str(
            payload.get("area") or "gallery"
        ).strip()[:40]

        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(
                session,
                slug,
            )

            if gallery is None:
                return {
                    "ok": False,
                    "reason": "gallery_missing",
                }

            profile = verified_customer_profile(
                session,
                request,
            )

            if profile is None:
                return {
                    "ok": False,
                    "reason": "not_verified",
                }

            activity = record_customer_activity(
                session,
                request,
                gallery,
                area=area,
                folder=folder_value,
                photo_view=(action == "photo_view"),
            )

            return {
                "ok": activity is not None,
                "photo_views": (
                    int(activity.photo_views or 0)
                    if activity else 0
                ),
                "favourites": (
                    int(activity.favourite_count or 0)
                    if activity else 0
                ),
                "basket_photos": (
                    int(activity.basket_photo_count or 0)
                    if activity else 0
                ),
            }


    @router.get("/g/{slug}/status", include_in_schema=False)
    async def public_gallery_status(request: Request, slug: str):
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return {"available": False, "reason": "database_unavailable"}
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            event = session.get(Event, gallery.event_id) if gallery else None
            asset_count = session.scalar(
                select(func.count(GalleryAsset.id)).where(
                    GalleryAsset.gallery_id == gallery.id, GalleryAsset.status == "ready"
                )
            ) if gallery else 0
            folder_count = session.scalar(
                select(func.count(GalleryFolder.id)).where(
                    GalleryFolder.gallery_id == gallery.id, GalleryFolder.active.is_(True)
                )
            ) if gallery else 0
        available = bool(gallery and gallery.status == "published" and not (gallery.expires_at and gallery.expires_at < date.today()))
        unlocked = bool(gallery and (gallery.visibility == "public" or request.session.get(f"gallery_access_{gallery.id}") is True))
        if not available or not unlocked:
            return {"available": False}
        return {
            "available": available,
            "asset_count": int(asset_count or 0),
            "folder_count": int(folder_count or 0),
            "gallery_status": gallery.status if gallery else "missing",
            "event_status": event.status if event else "missing",
            "is_live": bool(event and event.status == "live"),
            "expires_at": gallery.expires_at.isoformat() if gallery and gallery.expires_at else None,
            "customers_browsing": int(event.mirrored_customers_browsing or 0) if event else 0,
            "active_favourites": int(event.mirrored_active_favourites or 0) if event else 0,
            "active_baskets": int(event.mirrored_active_baskets or 0) if event else 0,
            "orders_today": int(event.mirrored_orders_today or 0) if event else 0,
            "last_sync_at": event.mirrored_last_sync_at.isoformat() if event and event.mirrored_last_sync_at else None,
        }


    @router.get("/g/{slug}/assets/{asset_id}", include_in_schema=False)
    async def public_gallery_asset(request: Request, slug: str, asset_id: str):
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
            asset = session.get(GalleryAsset, asset_id) if gallery else None
        if (gallery is None or gallery.status != "published" or
                (gallery.expires_at and gallery.expires_at < date.today()) or
                asset is None or asset.gallery_id != gallery.id or asset.status != "ready"):
            return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
        unlocked = gallery.visibility == "public" or request.session.get(f"gallery_access_{gallery.id}") is True
        if not unlocked:
            return RedirectResponse(f"/g/{slug}", status_code=303)
        storage_location = str(asset.storage_path or "")

        if storage_location.startswith("spaces://"):
            signed_url = presigned_get_url(
                request.app.state.settings,
                storage_location,
                expires_seconds=300,
            )
            return RedirectResponse(
                signed_url,
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Cache-Control": "private, no-store"},
            )

        path = Path(storage_location)
        if not path.is_file():
            return templates.TemplateResponse(
                request=request,
                name="404.html",
                context={},
                status_code=404,
            )

        return FileResponse(
            path,
            media_type=asset.content_type,
            filename=asset.filename,
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/g/{slug}/unlock", include_in_schema=False)
    async def public_gallery_unlock(request: Request, slug: str, access_code: str = Form("")):
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return templates.TemplateResponse(request=request, name="gallery_unavailable.html", context={}, status_code=503)
        with request.app.state.session_factory() as session:
            gallery = get_gallery_by_slug(session, slug)
        if gallery is None or gallery.status != "published":
            return RedirectResponse(f"/g/{slug}", status_code=303)
        if verify_access_code(gallery, access_code.strip()):
            request.session[f"gallery_access_{gallery.id}"] = True
            return RedirectResponse(f"/g/{slug}", status_code=303)
        return RedirectResponse(f"/g/{slug}?error=" + quote("That access code is not correct."), status_code=303)

    return router
