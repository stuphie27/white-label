from __future__ import annotations

import hmac
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.service import new_csrf_token
from app.db.models import Gallery
from app.db.session import database_is_ready, database_schema_is_ready
from app.transfers.service import (
    TRANSFER_DESTINATIONS,
    TRANSFER_STATUSES,
    create_transfer_job,
    expire_due_jobs,
    get_transfer_job,
    list_transfer_jobs,
    update_transfer_status,
)


def _require_staff(request: Request):
    if not request.session.get("staff_authenticated"):
        return RedirectResponse("/staff/login", status_code=status.HTTP_303_SEE_OTHER)
    return None


def _csrf(request: Request) -> str:
    token = new_csrf_token()
    request.session["transfer_csrf_token"] = token
    return token


def _valid_csrf(request: Request, supplied: str) -> bool:
    expected = str(request.session.get("transfer_csrf_token", ""))
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


def build_transfers_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(tags=["transfers"])

    @router.get("/staff/transfers", response_class=HTMLResponse, include_in_schema=False)
    async def transfers_index(request: Request, status_filter: str = ""):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            expire_due_jobs(session)
            jobs = list_transfer_jobs(session, status=status_filter)
            gallery_map = {gallery.id: gallery for gallery in session.query(Gallery).all()}
        return templates.TemplateResponse(request=request, name="transfers_list.html", context={
            "jobs": jobs,
            "gallery_map": gallery_map,
            "statuses": TRANSFER_STATUSES,
            "status_filter": status_filter,
            "csrf_token": _csrf(request),
            "staff_email": request.session.get("staff_email", ""),
        })

    @router.post("/staff/transfers/new", include_in_schema=False)
    async def transfer_create(
        request: Request,
        gallery_id: str = Form(...),
        destination: str = Form("customer"),
        item_count: int = Form(0),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse("/staff/transfers?error=" + quote("The form expired. Please try again."), status_code=303)
        with request.app.state.session_factory() as session:
            gallery = session.get(Gallery, gallery_id)
            if gallery is None or destination not in TRANSFER_DESTINATIONS:
                return RedirectResponse("/staff/galleries?error=" + quote("Choose a valid gallery and destination."), status_code=303)
            job = create_transfer_job(session, gallery_id=gallery.id, destination=destination, item_count=item_count)
        return RedirectResponse(f"/staff/transfers/{job.id}", status_code=303)

    @router.get("/staff/transfers/{job_id}", response_class=HTMLResponse, include_in_schema=False)
    async def transfer_detail(request: Request, job_id: str):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            expire_due_jobs(session)
            job = get_transfer_job(session, job_id)
            gallery = session.get(Gallery, job.gallery_id) if job else None
        if job is None:
            return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
        return templates.TemplateResponse(request=request, name="transfer_detail.html", context={
            "job": job,
            "gallery": gallery,
            "statuses": TRANSFER_STATUSES,
            "csrf_token": _csrf(request),
            "staff_email": request.session.get("staff_email", ""),
        })

    @router.post("/staff/transfers/{job_id}/status", include_in_schema=False)
    async def transfer_status(request: Request, job_id: str, transfer_status: str = Form(...), csrf_token: str = Form(...)):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(f"/staff/transfers/{job_id}", status_code=303)
        with request.app.state.session_factory() as session:
            job = get_transfer_job(session, job_id)
            if job is None:
                return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
            update_transfer_status(session, job, transfer_status)
        return RedirectResponse(f"/staff/transfers/{job_id}", status_code=303)

    return router
