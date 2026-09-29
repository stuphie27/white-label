from __future__ import annotations

import time

import hashlib
import hmac
import json
import secrets
import smtplib
from pathlib import Path
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from datetime import date, datetime, time as dt_time, timezone, timedelta

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth.service import new_csrf_token
from app.payroll_requests import issue_secure_link
from app.profile_requests import create_remote_request as create_staff_profile_request
from app.storage import delete, download_to_temp, presigned_get_url, upload_bytes
from app.staff_photos import normalise_staff_photo
from app.db.models import (
    Event,
    EventAvailabilityRequest,
    EventStaffAssignment,
    EventStaffShift,
    StaffMember,
    StaffPasswordReset,
    StaffPayrollSubmission,
    StaffPaymentProfile,
    StaffPayRate,
    StaffProfileRequest,
    StaffAdditionalWork,
    StaffDocumentView,
    StaffDocument,
    StaffDocumentAcknowledgement,

    StaffEventInterest,)


STAFF_DOCUMENT_CATEGORIES = (
    "Policies",
    "Procedures",
    "Forms",
    "Health & Safety",
    "Training",
    "Staff Guidance",
)

STAFF_DOCUMENT_MAX_BYTES = 25 * 1024 * 1024

STAFF_DOCUMENT_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/jpeg",
    "image/png",
}


def build_team_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/staff/team", tags=["staff-team"])

    def require_staff(request: Request):
        if not request.session.get("staff_authenticated"):
            return RedirectResponse(
                "/staff/login",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return None

    def require_team_admin(request: Request):
        """
        Full staff records are management-only.

        Ordinary authenticated staff may use the shared Staff Hub
        directory and their own self-service pages, but may not
        access another person's private Staff Master record.
        """
        redirect = require_staff(request)

        if redirect:
            return redirect

        if str(
            request.session.get("staff_role", "")
        ).strip() != "super_admin":
            return RedirectResponse(
                "/staff/team",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        return None


    def common_context(request: Request) -> dict[str, object]:
        staff_role = str(
            request.session.get("staff_role", "")
        ).strip()

        return {
            "staff_email": request.session.get("staff_email", ""),
            "version": request.app.state.settings.version,
            "active_nav": "team",
            "is_super_admin": staff_role == "super_admin",
        }

    def team_csrf(request: Request) -> str:
        token = new_csrf_token()
        request.session["team_csrf_token"] = token
        return token

    def valid_team_csrf(
        request: Request,
        supplied: str,
    ) -> bool:
        expected = str(
            request.session.get("team_csrf_token", "")
        )
        return bool(
            expected
            and hmac.compare_digest(expected, supplied)
        )

    def send_staff_setup_email(
        request: Request,
        *,
        member: StaffMember,
        raw_token: str,
    ) -> None:
        settings = request.app.state.settings

        if not settings.smtp_host:
            raise RuntimeError(
                "SMTP is not configured for staff login setup."
            )

        setup_url = (
            settings.public_base_url.rstrip("/")
            + "/staff/reset-password/"
            + raw_token
        )

        display_name = (
            member.preferred_name
            or member.staff_name
            or "there"
        )

        message = EmailMessage()
        message["From"] = (
            f"{settings.smtp_from_name} "
            f"<{settings.smtp_from_email}>"
        )
        message["To"] = member.email.strip()
        message["Subject"] = "Set up your Stuphie Online login"

        message.set_content(
            f"Hello {display_name},\n\n"
            "Your Stuphie Online staff account is ready to set up.\n\n"
            "Use this secure link within 10 minutes to choose "
            "your password:\n\n"
            f"{setup_url}\n\n"
            "The link can only be used once.\n\n"
            "Once your password has been created, you can sign "
            "in to Stuphie Online."
        )

        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=20,
        ) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()

            if settings.smtp_username:
                smtp.login(
                    settings.smtp_username,
                    settings.smtp_password,
                )

            smtp.send_message(message)

    @router.get(
        "",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def team_list(request: Request):
        redirect = require_staff(request)
        if redirect:
            return redirect

        members: list[StaffMember] = []
        assignment_counts: dict[str, int] = {}
        availability_status: dict[str, str] = {}
        profile_status: dict[str, str] = {}
        directory_photo_available: dict[str, bool] = {}
        directory_telephone: dict[str, str] = {}

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is not None:
            with session_factory() as session:
                members = list(
                    session.scalars(
                        select(StaffMember).order_by(
                            StaffMember.active.desc(),
                            StaffMember.preferred_name.asc(),
                            StaffMember.staff_name.asc(),
                        )
                    )
                )

                for member in members:
                    assignments = list(
                        session.scalars(
                            select(EventStaffAssignment).where(
                                EventStaffAssignment.staff_member_id
                                == member.id
                            )
                        )
                    )
                    assignment_counts[member.id] = len(assignments)

                    availability = session.scalars(
                        select(EventAvailabilityRequest)
                        .where(
                            EventAvailabilityRequest.staff_source_ref
                            == member.staff_source_ref
                        )
                        .order_by(
                            EventAvailabilityRequest.created_at.desc()
                        )
                    ).first()

                    availability_status[member.id] = (
                        availability.availability_status
                        if availability
                        else "unknown"
                    )

                    profile_request = session.scalar(
                        select(StaffProfileRequest)
                        .where(
                            StaffProfileRequest.source_ref
                            == member.staff_source_ref
                        )
                        .order_by(
                            StaffProfileRequest.updated_at.desc()
                        )
                    )

                    profile_complete = bool(
                        member.profile_completed_at
                        or (
                            profile_request
                            and profile_request.status == "submitted"
                        )
                    )

                    profile_status[member.id] = (
                        "complete"
                        if profile_complete
                        else (
                            str(profile_request.status)
                            if profile_request
                            else "not_invited"
                        )
                    )

                    # Staff-entered profile information is private.
                    # It may be viewed by the employee themselves and
                    # authorised management only. It must never become
                    # ordinary staff-directory data.
                    is_management_view = (
                        str(
                            request.session.get(
                                "staff_role",
                                "",
                            )
                        ).strip()
                        == "super_admin"
                    )

                    directory_photo_available[member.id] = (
                        bool(
                            member.photo_storage_path
                            or (
                                profile_request
                                and profile_request.photo_storage_path
                            )
                        )
                        if is_management_view
                        else False
                    )

                    directory_telephone[member.id] = (
                        str(
                            member.telephone or ""
                        ).strip()[:80]
                        if is_management_view
                        else ""
                    )

            documents = list(
                session.scalars(
                    select(StaffDocument)
                    .where(
                        StaffDocument.active.is_(True)
                    )
                    .order_by(
                        StaffDocument.category.asc(),
                        StaffDocument.title.asc(),
                    )
                )
            )

            archived_documents = []

            document_view_counts = {}
            document_unique_viewers = {}
            document_view_history = {}

            document_ids = [
                document.id
                for document in documents
            ]

            if document_ids:
                views = list(
                    session.scalars(
                        select(StaffDocumentView)
                        .where(
                            StaffDocumentView.document_id.in_(
                                document_ids
                            )
                        )
                        .order_by(
                            StaffDocumentView.viewed_at.desc()
                        )
                    )
                )

                for view in views:
                    document_view_counts[
                        view.document_id
                    ] = (
                        document_view_counts.get(
                            view.document_id,
                            0,
                        )
                        + 1
                    )

                    viewer_key = (
                        view.staff_member_id
                        or view.staff_email
                    )

                    if viewer_key:
                        document_unique_viewers.setdefault(
                            view.document_id,
                            set(),
                        ).add(viewer_key)

                    document_view_history.setdefault(
                        view.document_id,
                        [],
                    ).append(view)

            document_unique_viewers = {
                document_id: len(viewers)
                for document_id, viewers
                in document_unique_viewers.items()
            }

            if str(
                request.session.get(
                    "staff_role",
                    "",
                )
            ).strip() == "super_admin":

                archived_documents = list(
                    session.scalars(
                        select(StaffDocument)
                        .where(
                            StaffDocument.active.is_(False)
                        )
                        .order_by(
                            StaffDocument.updated_at.desc()
                        )
                    )
                )

        context = common_context(request)
        context.update(
            {
                "members": members,
                "assignment_counts": assignment_counts,
                "availability_status": availability_status,
                "profile_status": profile_status,
                "directory_photo_available": directory_photo_available,
                "documents": documents,
                "archived_documents": archived_documents,
                "document_categories": STAFF_DOCUMENT_CATEGORIES,
                "document_view_counts": document_view_counts,
                "document_unique_viewers": document_unique_viewers,
                "document_view_history": document_view_history,
                "directory_telephone": directory_telephone,
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="team_list.html",
            context=context,
        )


    @router.get(
        "/my-documents",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def staff_my_documents(request: Request):
        redirect = require_staff(request)

        if redirect:
            return redirect

        staff_id = str(
            request.session.get("staff_id", "")
        ).strip()

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        documents = []
        acknowledgements = {}

        if session_factory is not None:
            with session_factory() as session:
                documents = list(
                    session.scalars(
                        select(StaffDocument)
                        .where(
                            StaffDocument.active.is_(True)
                        )
                        .order_by(
                            StaffDocument.category.asc(),
                            StaffDocument.title.asc(),
                        )
                    )
                )

                if staff_id:
                    rows = list(
                        session.scalars(
                            select(
                                StaffDocumentAcknowledgement
                            ).where(
                                StaffDocumentAcknowledgement.staff_member_id
                                == staff_id
                            )
                        )
                    )

                    acknowledgements = {
                        row.document_id: row
                        for row in rows
                    }

        context = common_context(request)

        context.update(
            {
                "documents": documents,
                "document_categories": STAFF_DOCUMENT_CATEGORIES,
                "acknowledgements": acknowledgements,
                "csrf_token": team_csrf(request),
                "active_nav": "my_documents",
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="staff_documents.html",
            context=context,
        )


    @router.post(
        "/my-documents/{document_id}/acknowledge",
        include_in_schema=False,
    )
    async def staff_document_acknowledge(
        request: Request,
        document_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        if not valid_team_csrf(request, csrf_token):
            return RedirectResponse(
                "/staff/team/my-documents?error=csrf",
                status_code=303,
            )

        staff_id = str(
            request.session.get("staff_id", "")
        ).strip()

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if not staff_id or session_factory is None:
            return RedirectResponse(
                "/staff/team/my-documents",
                status_code=303,
            )

        with session_factory() as session:
            document = session.get(
                StaffDocument,
                document_id,
            )

            member = session.get(
                StaffMember,
                staff_id,
            )

            if (
                document is None
                or not document.active
                or not document.requires_acknowledgement
                or member is None
            ):
                return RedirectResponse(
                    "/staff/team/my-documents",
                    status_code=303,
                )

            acknowledgement = session.scalar(
                select(
                    StaffDocumentAcknowledgement
                ).where(
                    StaffDocumentAcknowledgement.document_id
                    == document.id,
                    StaffDocumentAcknowledgement.staff_member_id
                    == member.id,
                )
            )

            if acknowledgement is None:
                acknowledgement = (
                    StaffDocumentAcknowledgement(
                        document_id=document.id,
                        staff_member_id=member.id,
                        staff_email=staff_email[:320],
                        document_version=str(
                            document.version or ""
                        )[:40],
                        acknowledged_at=datetime.now(
                            timezone.utc
                        ),
                    )
                )

                session.add(acknowledgement)

            else:
                acknowledgement.staff_email = (
                    staff_email[:320]
                )
                acknowledgement.document_version = str(
                    document.version or ""
                )[:40]
                acknowledgement.acknowledged_at = (
                    datetime.now(timezone.utc)
                )

            session.commit()

        return RedirectResponse(
            "/staff/team/my-documents"
            "?acknowledged=1",
            status_code=303,
        )


    @router.get(
        "/documents",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def staff_documents_management(
        request: Request,
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        if str(
            request.session.get(
                "staff_role",
                "",
            )
        ).strip() != "super_admin":
            return RedirectResponse(
                "/staff",
                status_code=303,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        documents = []
        archived_documents = []
        view_counts = {}
        unique_view_counts = {}
        acknowledgement_counts = {}

        if session_factory is not None:
            with session_factory() as session:
                documents = list(
                    session.scalars(
                        select(StaffDocument)
                        .where(
                            StaffDocument.active.is_(True)
                        )
                        .order_by(
                            StaffDocument.category.asc(),
                            StaffDocument.title.asc(),
                        )
                    )
                )

                archived_documents = list(
                    session.scalars(
                        select(StaffDocument)
                        .where(
                            StaffDocument.active.is_(False)
                        )
                        .order_by(
                            StaffDocument.updated_at.desc()
                        )
                    )
                )

                document_ids = [
                    document.id
                    for document in documents
                ]

                if document_ids:
                    views = list(
                        session.scalars(
                            select(StaffDocumentView)
                            .where(
                                StaffDocumentView.document_id.in_(
                                    document_ids
                                )
                            )
                        )
                    )

                    viewers = {}

                    for view in views:
                        view_counts[
                            view.document_id
                        ] = (
                            view_counts.get(
                                view.document_id,
                                0,
                            )
                            + 1
                        )

                        key = (
                            view.staff_member_id
                            or view.staff_email
                        )

                        if key:
                            viewers.setdefault(
                                view.document_id,
                                set(),
                            ).add(key)

                    unique_view_counts = {
                        document_id: len(values)
                        for document_id, values
                        in viewers.items()
                    }

                    acknowledgements = list(
                        session.scalars(
                            select(
                                StaffDocumentAcknowledgement
                            ).where(
                                StaffDocumentAcknowledgement.document_id.in_(
                                    document_ids
                                )
                            )
                        )
                    )

                    for row in acknowledgements:
                        acknowledgement_counts[
                            row.document_id
                        ] = (
                            acknowledgement_counts.get(
                                row.document_id,
                                0,
                            )
                            + 1
                        )

        context = common_context(request)

        context.update(
            {
                "documents": documents,
                "archived_documents": archived_documents,
                "document_categories": STAFF_DOCUMENT_CATEGORIES,
                "document_view_counts": view_counts,
                "document_unique_viewers": unique_view_counts,
                "document_acknowledgement_counts": acknowledgement_counts,
                "csrf_token": team_csrf(request),
                "active_nav": "documents",
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="staff_documents_management.html",
            context=context,
        )


    @router.get(
        "/documents/{document_id}/view",
        include_in_schema=False,
    )
    async def staff_document_view(
        request: Request,
        document_id: str,
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        staff_id = str(
            request.session.get(
                "staff_id",
                "",
            )
        ).strip()

        staff_email = str(
            request.session.get(
                "staff_email",
                "",
            )
        ).strip().lower()

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team/my-documents",
                status_code=303,
            )

        with session_factory() as session:

            document = session.get(
                StaffDocument,
                document_id,
            )

            if document is None:
                return RedirectResponse(
                    "/staff/team/my-documents",
                    status_code=303,
                )

            is_admin = str(
                request.session.get(
                    "staff_role",
                    "",
                )
            ).strip() == "super_admin"

            if (
                not document.active
                and not is_admin
            ):
                return RedirectResponse(
                    "/staff/team/my-documents",
                    status_code=303,
                )

            member = None

            if staff_id:
                member = session.get(
                    StaffMember,
                    staff_id,
                )

            if (
                member is None
                and staff_email
            ):
                member = session.scalar(
                    select(StaffMember)
                    .where(
                        StaffMember.email.ilike(
                            staff_email
                        )
                    )
                )

            display_name = (
                (
                    member.preferred_name
                    or member.staff_name
                )
                if member
                else staff_email
            )

            view_row = StaffDocumentView(
                document_id=document.id,
                staff_member_id=(
                    member.id
                    if member
                    else None
                ),
                staff_email=staff_email[:320],
                staff_name=str(
                    display_name or "Staff Member"
                )[:200],
                viewed_at=datetime.now(
                    timezone.utc
                ),
            )

            session.add(view_row)
            session.commit()

            try:
                signed_url = presigned_get_url(
                    request.app.state.settings,
                    document.storage_path,
                    expires_seconds=180,
                )

            except Exception:
                return RedirectResponse(
                    "/staff/team"
                    "?document=view_failed"
                    "#documents",
                    status_code=303,
                )

        return RedirectResponse(
            signed_url,
            status_code=303,
        )


    @router.post(
        "/documents/upload",
        include_in_schema=False,
    )
    async def staff_document_upload(
        request: Request,
        csrf_token: str = Form(...),
        title: str = Form(...),
        category: str = Form(...),
        description: str = Form(default=""),
        version: str = Form(default="1.0"),
        requires_acknowledgement: str = Form(default=""),
        document: UploadFile = File(...),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                "/staff/team/documents"
                "?document=csrf",
                status_code=303,
            )

        clean_title = str(
            title or ""
        ).strip()[:240]

        clean_category = str(
            category or ""
        ).strip()

        clean_description = str(
            description or ""
        ).strip()

        clean_version = str(
            version or "1.0"
        ).strip()[:40]

        if not clean_title:
            return RedirectResponse(
                "/staff/team/documents"
                "?document=title",
                status_code=303,
            )

        if (
            clean_category
            not in STAFF_DOCUMENT_CATEGORIES
        ):
            return RedirectResponse(
                "/staff/team/documents"
                "?document=category",
                status_code=303,
            )

        original_filename = Path(
            document.filename
            or "document"
        ).name[:240]

        content_type = str(
            document.content_type
            or "application/octet-stream"
        ).lower()

        if (
            content_type
            not in STAFF_DOCUMENT_CONTENT_TYPES
        ):
            return RedirectResponse(
                "/staff/team/documents"
                "?document=type",
                status_code=303,
            )

        content = await document.read(
            STAFF_DOCUMENT_MAX_BYTES + 1
        )

        if not content:
            return RedirectResponse(
                "/staff/team/documents"
                "?document=empty",
                status_code=303,
            )

        if (
            len(content)
            > STAFF_DOCUMENT_MAX_BYTES
        ):
            return RedirectResponse(
                "/staff/team/documents"
                "?document=size",
                status_code=303,
            )

        document_key = secrets.token_hex(16)

        suffix = Path(
            original_filename
        ).suffix.lower()[:12]

        storage_key = (
            "staff-documents/"
            f"{document_key}/"
            f"document{suffix}"
        )

        try:
            storage_location = upload_bytes(
                request.app.state.settings,
                storage_key,
                content,
                content_type,
            )

        except Exception:
            return RedirectResponse(
                "/staff/team/documents"
                "?document=storage",
                status_code=303,
            )

        try:
            with (
                request.app.state.session_factory()
                as session
            ):

                row = StaffDocument(
                    title=clean_title,
                    category=clean_category,
                    description=clean_description,
                    version=clean_version,
                    storage_path=storage_location,
                    filename=original_filename,
                    content_type=content_type,
                    size_bytes=len(content),
                    active=True,
                    requires_acknowledgement=(
                        str(
                            requires_acknowledgement
                            or ""
                        ).strip().lower()
                        in {
                            "1",
                            "true",
                            "yes",
                            "on",
                        }
                    ),
                    uploaded_by=str(
                        request.session.get(
                            "staff_email",
                            "",
                        )
                    )[:320],
                )

                session.add(row)
                session.commit()

        except Exception:

            try:
                delete(
                    request.app.state.settings,
                    storage_location,
                )
            except Exception:
                pass

            return RedirectResponse(
                "/staff/team/documents"
                "?document=database",
                status_code=303,
            )

        return RedirectResponse(
            "/staff/team/documents"
            "?document=uploaded",
            status_code=303,
        )


    @router.post(
        "/documents/{document_id}/archive",
        include_in_schema=False,
    )
    async def staff_document_archive(
        request: Request,
        document_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                "/staff/team/documents",
                status_code=303,
            )

        with (
            request.app.state.session_factory()
            as session
        ):

            document = session.get(
                StaffDocument,
                document_id,
            )

            if document is not None:
                document.active = False
                document.updated_at = (
                    datetime.now(
                        timezone.utc
                    )
                )
                session.commit()

        return RedirectResponse(
            "/staff/team/documents"
            "?document=archived",
            status_code=303,
        )


    @router.post(
        "/documents/{document_id}/restore",
        include_in_schema=False,
    )
    async def staff_document_restore(
        request: Request,
        document_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                "/staff/team/documents",
                status_code=303,
            )

        with (
            request.app.state.session_factory()
            as session
        ):

            document = session.get(
                StaffDocument,
                document_id,
            )

            if document is not None:
                document.active = True
                document.updated_at = (
                    datetime.now(
                        timezone.utc
                    )
                )
                session.commit()

        return RedirectResponse(
            "/staff/team/documents"
            "?document=restored",
            status_code=303,
        )


    @router.get(
        "/{person_id}/directory-photo",
        include_in_schema=False,
    )
    async def team_directory_photo(
        request: Request,
        person_id: str,
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team",
                status_code=303,
            )

        with session_factory() as session:
            member = session.get(
                StaffMember,
                person_id,
            )

            if member is None or not member.active:
                return RedirectResponse(
                    "/staff/team",
                    status_code=303,
                )

            viewer_role = str(
                request.session.get(
                    "staff_role",
                    "",
                )
            ).strip()

            viewer_staff_id = str(
                request.session.get(
                    "staff_id",
                    "",
                )
            ).strip()

            if (
                viewer_role != "super_admin"
                and viewer_staff_id != member.id
            ):
                return RedirectResponse(
                    "/static/images/staff-placeholder.svg",
                    status_code=303,
                )

            location = str(
                member.photo_storage_path or ""
            ).strip()

            if not location:
                profile_request = session.scalar(
                    select(StaffProfileRequest)
                    .where(
                        StaffProfileRequest.source_ref
                        == member.staff_source_ref
                    )
                    .order_by(
                        StaffProfileRequest.updated_at.desc()
                    )
                )

                if profile_request is not None:
                    location = str(
                        profile_request.photo_storage_path or ""
                    ).strip()

            if not location:
                return RedirectResponse(
                    "/static/images/staff-placeholder.svg",
                    status_code=303,
                )

            if not location.startswith("spaces://"):
                return RedirectResponse(
                    "/static/images/staff-placeholder.svg",
                    status_code=303,
                )

            try:
                signed_url = presigned_get_url(
                    request.app.state.settings,
                    location,
                    expires_seconds=300,
                )
            except Exception:
                return RedirectResponse(
                    "/static/images/staff-placeholder.svg",
                    status_code=303,
                )

        return RedirectResponse(
            signed_url,
            status_code=302,
        )


    @router.get(
        "/profile",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def staff_profile(request: Request):
        redirect = require_staff(request)

        if redirect:
            return redirect

        staff_id = str(
            request.session.get("staff_id", "")
        ).strip()

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        member = None
        profile_data: dict[str, str] = {}

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is not None:
            with session_factory() as session:

                if staff_id:
                    member = session.get(
                        StaffMember,
                        staff_id,
                    )

                if member is None and staff_email:
                    member = session.scalar(
                        select(StaffMember).where(
                            StaffMember.email.ilike(
                                staff_email
                            )
                        )
                    )

                if member is not None:
                    try:
                        profile_data = json.loads(
                            member.private_profile_json
                            or "{}"
                        )
                    except (
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ):
                        profile_data = {}

                    # Compatibility with staff who completed the
                    # secure profile before Staff Hub V1 existed.
                    if not profile_data:
                        previous = session.scalar(
                            select(StaffProfileRequest)
                            .where(
                                StaffProfileRequest.source_ref
                                == member.staff_source_ref
                            )
                            .order_by(
                                StaffProfileRequest.updated_at.desc()
                            )
                        )

                        if (
                            previous is not None
                            and previous.payload_json
                        ):
                            try:
                                profile_data = json.loads(
                                    previous.payload_json
                                )
                            except (
                                TypeError,
                                ValueError,
                                json.JSONDecodeError,
                            ):
                                profile_data = {}

        context = common_context(request)

        context.update(
            {
                "member": member,
                "profile_data": profile_data,
                "csrf_token": team_csrf(request),
                "active_nav": "profile",
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="staff_profile.html",
            context=context,
        )


    @router.post(
        "/profile",
        include_in_schema=False,
    )
    async def staff_profile_save(
        request: Request,
        csrf_token: str = Form(...),
        preferred_name: str = Form(""),
        telephone: str = Form(""),
        address_line_1: str = Form(""),
        address_line_2: str = Form(""),
        town_city: str = Form(""),
        postcode: str = Form(""),
        date_of_birth: str = Form(""),
        emergency_name: str = Form(""),
        emergency_relationship: str = Form(""),
        emergency_phone: str = Form(""),
        secondary_emergency_name: str = Form(""),
        secondary_emergency_phone: str = Form(""),
        allergies: str = Form(""),
        medical_notes: str = Form(""),
        accessibility_requirements: str = Form(""),
        dietary_requirements: str = Form(""),
        driving_status: str = Form(""),
        uniform_size: str = Form(""),
        photo: UploadFile | None = File(default=None),
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                "/staff/team/profile",
                status_code=303,
            )

        staff_id = str(
            request.session.get("staff_id", "")
        ).strip()

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team/profile",
                status_code=303,
            )

        with session_factory() as session:

            member = None

            if staff_id:
                member = session.get(
                    StaffMember,
                    staff_id,
                )

            if member is None and staff_email:
                member = session.scalar(
                    select(StaffMember).where(
                        StaffMember.email.ilike(
                            staff_email
                        )
                    )
                )

            if member is None:
                return RedirectResponse(
                    "/staff/team/profile",
                    status_code=303,
                )

            payload = {
                "preferred_name":
                    preferred_name.strip()[:160],
                "email":
                    str(member.email or "").strip().lower()[:240],
                "telephone":
                    telephone.strip()[:80],
                "address_line_1":
                    address_line_1.strip()[:240],
                "address_line_2":
                    address_line_2.strip()[:240],
                "town_city":
                    town_city.strip()[:160],
                "postcode":
                    postcode.strip()[:40],
                "date_of_birth":
                    date_of_birth.strip()[:20],
                "emergency_name":
                    emergency_name.strip()[:160],
                "emergency_relationship":
                    emergency_relationship.strip()[:120],
                "emergency_phone":
                    emergency_phone.strip()[:80],
                "secondary_emergency_name":
                    secondary_emergency_name.strip()[:160],
                "secondary_emergency_phone":
                    secondary_emergency_phone.strip()[:80],
                "allergies":
                    allergies.strip()[:2000],
                "medical_notes":
                    medical_notes.strip()[:3000],
                "accessibility_requirements":
                    accessibility_requirements.strip()[:2000],
                "dietary_requirements":
                    dietary_requirements.strip()[:1500],
                "driving_status":
                    driving_status.strip()[:80],
                "uniform_size":
                    uniform_size.strip()[:80],
            }

            member.preferred_name = (
                payload["preferred_name"]
                or member.preferred_name
            )

            member.telephone = payload["telephone"]

            member.private_profile_json = json.dumps(
                payload,
                separators=(",", ":"),
            )

            member.profile_completed_at = (
                member.profile_completed_at
                or datetime.now(timezone.utc)
            )

            if photo and photo.filename:
                filename = Path(
                    photo.filename
                ).name

                suffix = Path(
                    filename
                ).suffix.lower()

                if suffix not in {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".heic",
                    ".heif",
                }:
                    return RedirectResponse(
                        "/staff/team/profile?error=photo_type",
                        status_code=303,
                    )

                content = await photo.read(
                    12 * 1024 * 1024 + 1
                )

                if len(content) > (
                    12 * 1024 * 1024
                ):
                    return RedirectResponse(
                        "/staff/team/profile?error=photo_size",
                        status_code=303,
                    )

                try:
                    content, filename, content_type = (
                        normalise_staff_photo(
                            content,
                            filename,
                        )
                    )
                except ValueError:
                    return RedirectResponse(
                        "/staff/team/profile?error=photo_type",
                        status_code=303,
                    )

                suffix = ".jpg"

                object_key = (
                    "staff-members/"
                    f"{member.id}/"
                    f"{secrets.token_hex(12)}"
                    f"{suffix}"
                )

                location = upload_bytes(
                    request.app.state.settings,
                    object_key,
                    content,
                    content_type,
                )

                old_location = str(
                    member.photo_storage_path or ""
                )

                member.photo_storage_path = location
                member.photo_filename = (
                    filename[:240]
                )
                member.photo_content_type = (
                    content_type[:120]
                )

                session.commit()

                if (
                    old_location
                    and old_location != location
                ):
                    try:
                        delete(
                            request.app.state.settings,
                            old_location,
                        )
                    except Exception:
                        pass

            else:
                session.commit()

        return RedirectResponse(
            "/staff/team/profile?saved=1",
            status_code=303,
        )



    # ========================================================
    # STAFF EVENT PORTAL
    #
    # Employee-safe read-only view of Event Master.
    # The only write operation is Register / Withdraw Interest.
    # ========================================================

    @router.get(
        "/events",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def staff_events_portal(request: Request):

        redirect = require_staff(request)

        if redirect:
            return redirect

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        member = None
        events = []
        interest_event_ids = set()
        assigned_event_ids = set()
        calendar_months = []

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is not None:

            with session_factory() as session:

                member = session.scalar(
                    select(StaffMember).where(
                        StaffMember.email.ilike(
                            staff_email
                        )
                    )
                )

                events = list(
                    session.scalars(
                        select(Event)
                        .where(
                            Event.status.notin_(
                                ("archived", "cancelled")
                            ),
                            Event.end_date >= date.today(),
                        )
                        .order_by(
                            Event.start_date.asc(),
                            Event.name.asc(),
                        )
                    )
                )

                if member is not None:

                    interests = list(
                        session.scalars(
                            select(StaffEventInterest).where(
                                StaffEventInterest.staff_member_id
                                == member.id,
                                StaffEventInterest.status
                                == "interested",
                            )
                        )
                    )

                    interest_event_ids = {
                        row.event_id
                        for row in interests
                    }

                    assignments = list(
                        session.scalars(
                            select(EventStaffAssignment).where(
                                EventStaffAssignment.staff_member_id
                                == member.id,
                                EventStaffAssignment.assignment_status
                                == "assigned",
                            )
                        )
                    )

                    assigned_event_ids = {
                        row.event_id
                        for row in assignments
                    }

                event_rows = []

                availability_state_by_event = {}

                if member is not None:
                    staff_ref = str(member.staff_source_ref or "").strip()

                    if staff_ref:
                        availability_rows = list(
                            session.scalars(
                                select(EventAvailabilityRequest)
                                .where(
                                    EventAvailabilityRequest.staff_source_ref
                                    == staff_ref
                                )
                                .order_by(
                                    EventAvailabilityRequest.updated_at.desc()
                                )
                            )
                        )

                        latest_by_event_ref = {}

                        for availability_row in availability_rows:
                            event_ref = str(
                                availability_row.event_source_ref or ""
                            ).strip()

                            if (
                                event_ref
                                and event_ref not in latest_by_event_ref
                            ):
                                latest_by_event_ref[event_ref] = (
                                    availability_row
                                )

                        for event_ref, availability_row in (
                            latest_by_event_ref.items()
                        ):
                            availability = str(
                                availability_row.availability_status or ""
                            ).strip().lower()

                            request_status = str(
                                availability_row.status or ""
                            ).strip().lower()

                            if (
                                request_status == "submitted"
                                and availability == "available"
                            ):
                                state = "available"

                            elif (
                                request_status == "submitted"
                                and availability == "unavailable"
                            ):
                                state = "unavailable"

                            elif request_status == "email_failed":
                                state = "email_failed"

                            else:
                                state = "waiting"

                            availability_state_by_event[event_ref] = state

                for event in events:
                    interested_staff = session.execute(
                        select(StaffEventInterest, StaffMember)
                        .join(
                            StaffMember,
                            StaffMember.id == StaffEventInterest.staff_member_id,
                        )
                        .where(
                            StaffEventInterest.event_id == event.id,
                            StaffEventInterest.status == "interested",
                        )
                        .order_by(
                            StaffMember.preferred_name.asc(),
                            StaffMember.staff_name.asc(),
                        )
                    ).all()

                    confirmed_staff = session.execute(
                        select(EventStaffAssignment, StaffMember)
                        .join(
                            StaffMember,
                            StaffMember.id == EventStaffAssignment.staff_member_id,
                        )
                        .where(
                            EventStaffAssignment.event_id == event.id,
                            EventStaffAssignment.assignment_status == "assigned",
                        )
                        .order_by(
                            StaffMember.preferred_name.asc(),
                            StaffMember.staff_name.asc(),
                        )
                    ).all()

                    confirmed_staff_ids = {
                        staff.id
                        for _assignment, staff in confirmed_staff
                    }
                    interested_staff = [
                        (interest, staff)
                        for interest, staff in interested_staff
                        if staff.id not in confirmed_staff_ids
                    ]

                    event_ref = str(
                        event.source_ref or ""
                    ).strip()

                    availability_state = (
                        availability_state_by_event.get(
                            event_ref,
                            "waiting",
                        )
                    )

                    is_assigned = event.id in assigned_event_ids

                    is_confirmed = (
                        is_assigned
                        and availability_state == "available"
                    )

                    is_waiting_confirmation = (
                        is_assigned
                        and availability_state == "waiting"
                    )

                    is_unavailable = (
                        is_assigned
                        and availability_state == "unavailable"
                    )

                    is_email_failed = (
                        is_assigned
                        and availability_state == "email_failed"
                    )

                    truly_confirmed_staff = []

                    for assignment, staff in confirmed_staff:
                        staff_ref = str(
                            staff.staff_source_ref or ""
                        ).strip()

                        staff_state = "waiting"

                        if staff_ref and event_ref:
                            staff_rows = list(
                                session.scalars(
                                    select(EventAvailabilityRequest)
                                    .where(
                                        EventAvailabilityRequest.event_source_ref
                                        == event_ref,
                                        EventAvailabilityRequest.staff_source_ref
                                        == staff_ref,
                                    )
                                    .order_by(
                                        EventAvailabilityRequest.updated_at.desc()
                                    )
                                )
                            )

                            if staff_rows:
                                latest = staff_rows[0]

                                latest_availability = str(
                                    latest.availability_status or ""
                                ).strip().lower()

                                latest_status = str(
                                    latest.status or ""
                                ).strip().lower()

                                if (
                                    latest_status == "submitted"
                                    and latest_availability == "available"
                                ):
                                    staff_state = "available"

                        if staff_state == "available":
                            truly_confirmed_staff.append(
                                (assignment, staff)
                            )

                    event_rows.append(
                        {
                            "event": event,
                            "interested_staff": interested_staff,
                            "confirmed_staff": confirmed_staff,
                            "truly_confirmed_staff": truly_confirmed_staff,
                            "is_interested": event.id in interest_event_ids,
                            "is_assigned": is_assigned,
                            "is_confirmed": is_confirmed,
                            "is_waiting_confirmation": is_waiting_confirmation,
                            "is_unavailable": is_unavailable,
                            "is_email_failed": is_email_failed,
                            "availability_state": availability_state,
                        }
                    )

        # Build server-side month calendars.
        month_keys = []

        for event in events:
            key = (
                event.start_date.year,
                event.start_date.month,
            )

            if key not in month_keys:
                month_keys.append(key)

        import calendar as calendar_module

        for year, month in month_keys:

            month_events = [
                event
                for event in events
                if (
                    event.start_date.year,
                    event.start_date.month,
                )
                == (year, month)
            ]

            weeks = []

            for raw_week in calendar_module.monthcalendar(
                year,
                month,
            ):
                week = []

                for day_number in raw_week:

                    if day_number == 0:
                        week.append(None)
                        continue

                    current_date = date(
                        year,
                        month,
                        day_number,
                    )

                    day_events = [
                        event
                        for event in events
                        if (
                            event.start_date
                            <= current_date
                            <= event.end_date
                        )
                    ]

                    week.append(
                        {
                            "number": day_number,
                            "date": current_date,
                            "events": day_events,
                        }
                    )

                weeks.append(week)

            calendar_months.append(
                {
                    "year": year,
                    "month": month,
                    "label": date(
                        year,
                        month,
                        1,
                    ).strftime("%B %Y"),
                    "weeks": weeks,
                    "events": month_events,
                }
            )

        context = common_context(request)

        context.update(
            {
                "active_nav": "staff_events",
                "member": member,
                "events": events,
                "event_rows": event_rows,
                "calendar_months": calendar_months,
                "interest_event_ids":
                    interest_event_ids,
                "assigned_event_ids":
                    assigned_event_ids,
                "csrf_token": team_csrf(request),
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="staff_events.html",
            context=context,
        )


    @router.get(
        "/events/calendar",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def staff_events_calendar(request: Request):

        redirect = require_staff(request)

        if redirect:
            return redirect

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        member = None
        events = []
        interest_event_ids = set()
        assigned_event_ids = set()
        confirmed_event_ids = set()
        calendar_months = []

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is not None:

            with session_factory() as session:

                member = session.scalar(
                    select(StaffMember).where(
                        StaffMember.email.ilike(
                            staff_email
                        )
                    )
                )

                events = list(
                    session.scalars(
                        select(Event)
                        .where(
                            Event.status.notin_(
                                ("archived", "cancelled")
                            ),
                            Event.end_date >= date.today(),
                        )
                        .order_by(
                            Event.start_date.asc(),
                            Event.name.asc(),
                        )
                    )
                )

                if member is not None:

                    interests = list(
                        session.scalars(
                            select(StaffEventInterest).where(
                                StaffEventInterest.staff_member_id
                                == member.id,
                                StaffEventInterest.status
                                == "interested",
                            )
                        )
                    )

                    interest_event_ids = {
                        row.event_id
                        for row in interests
                    }

                    assignments = list(
                        session.scalars(
                            select(EventStaffAssignment).where(
                                EventStaffAssignment.staff_member_id
                                == member.id,
                                EventStaffAssignment.assignment_status
                                == "assigned",
                            )
                        )
                    )

                    assigned_event_ids = {
                        row.event_id
                        for row in assignments
                    }

                    staff_ref = str(
                        member.staff_source_ref or ""
                    ).strip()

                    if staff_ref:

                        availability_rows = list(
                            session.scalars(
                                select(EventAvailabilityRequest)
                                .where(
                                    EventAvailabilityRequest.staff_source_ref
                                    == staff_ref
                                )
                                .order_by(
                                    EventAvailabilityRequest.updated_at.desc()
                                )
                            )
                        )

                        latest_by_event_ref = {}

                        for availability_row in availability_rows:

                            event_ref = str(
                                availability_row.event_source_ref or ""
                            ).strip()

                            if (
                                event_ref
                                and event_ref not in latest_by_event_ref
                            ):
                                latest_by_event_ref[event_ref] = (
                                    availability_row
                                )

                        event_id_by_ref = {
                            str(event.source_ref or "").strip(): event.id
                            for event in events
                            if str(event.source_ref or "").strip()
                        }

                        for event_ref, availability_row in (
                            latest_by_event_ref.items()
                        ):

                            request_status = str(
                                availability_row.status or ""
                            ).strip().lower()

                            availability_status = str(
                                availability_row.availability_status or ""
                            ).strip().lower()

                            event_id = event_id_by_ref.get(event_ref)

                            if (
                                event_id in assigned_event_ids
                                and request_status == "submitted"
                                and availability_status == "available"
                            ):
                                confirmed_event_ids.add(event_id)

        import calendar as calendar_module

        month_keys = []

        for event in events:

            key = (
                event.start_date.year,
                event.start_date.month,
            )

            if key not in month_keys:
                month_keys.append(key)

        for year, month in month_keys:

            month_events = [
                event
                for event in events
                if (
                    event.start_date.year,
                    event.start_date.month,
                ) == (year, month)
            ]

            weeks = []

            for raw_week in calendar_module.monthcalendar(
                year,
                month,
            ):

                week = []

                for day_number in raw_week:

                    if day_number == 0:
                        week.append(None)
                        continue

                    current_date = date(
                        year,
                        month,
                        day_number,
                    )

                    day_events = [
                        event
                        for event in events
                        if (
                            event.start_date
                            <= current_date
                            <= event.end_date
                        )
                    ]

                    week.append(
                        {
                            "number": day_number,
                            "date": current_date,
                            "events": day_events,
                        }
                    )

                weeks.append(week)

            calendar_months.append(
                {
                    "year": year,
                    "month": month,
                    "label": date(
                        year,
                        month,
                        1,
                    ).strftime("%B %Y"),
                    "weeks": weeks,
                    "events": month_events,
                }
            )

        context = common_context(request)

        context.update(
            {
                "active_nav": "staff_events",
                "member": member,
                "events": events,
                "calendar_months": calendar_months,
                "interest_event_ids":
                    interest_event_ids,
                "assigned_event_ids":
                    assigned_event_ids,
                "confirmed_event_ids":
                    confirmed_event_ids,
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="staff_events_calendar.html",
            context=context,
        )


    @router.get(
        "/events/{event_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def staff_event_view(
        request: Request,
        event_id: str,
    ):

        redirect = require_staff(request)

        if redirect:
            return redirect

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        member = None
        event = None
        interested = False
        assigned = False
        assigned_team = []
        assigned_count = 0

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is not None:

            with session_factory() as session:

                member = session.scalar(
                    select(StaffMember).where(
                        StaffMember.email.ilike(
                            staff_email
                        )
                    )
                )

                event = session.get(
                    Event,
                    event_id,
                )

                if (
                    event is not None
                    and event.status == "archived"
                ):
                    event = None

                if (
                    member is not None
                    and event is not None
                ):

                    interest = session.scalar(
                        select(StaffEventInterest).where(
                            StaffEventInterest.event_id
                            == event.id,
                            StaffEventInterest.staff_member_id
                            == member.id,
                        )
                    )

                    interested = bool(
                        interest
                        and interest.status
                        == "interested"
                    )

                    assignment = session.scalar(
                        select(EventStaffAssignment).where(
                            EventStaffAssignment.event_id
                            == event.id,
                            EventStaffAssignment.staff_member_id
                            == member.id,
                            EventStaffAssignment.assignment_status
                            == "assigned",
                        )
                    )

                    assigned = assignment is not None

                if event is not None:

                    rows = session.execute(
                        select(
                            EventStaffAssignment,
                            StaffMember,
                        )
                        .join(
                            StaffMember,
                            StaffMember.id
                            == EventStaffAssignment.staff_member_id,
                        )
                        .where(
                            EventStaffAssignment.event_id
                            == event.id,
                            EventStaffAssignment.assignment_status
                            == "assigned",
                        )
                        .order_by(
                            StaffMember.preferred_name.asc(),
                            StaffMember.staff_name.asc(),
                        )
                    ).all()

                    assigned_team = rows
                    assigned_count = len(rows)

        if event is None:
            return templates.TemplateResponse(
                request=request,
                name="404.html",
                context={},
                status_code=404,
            )

        context = common_context(request)

        context.update(
            {
                "active_nav": "staff_events",
                "member": member,
                "event": event,
                "interested": interested,
                "assigned": assigned,
                "assigned_team": assigned_team,
                "assigned_count": assigned_count,
                "csrf_token": team_csrf(request),
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="staff_event_detail.html",
            context=context,
        )


    @router.post(
        "/events/{event_id}/interest",
        include_in_schema=False,
    )
    async def staff_event_interest(
        request: Request,
        event_id: str,
        action: str = Form(...),
        csrf_token: str = Form(...),
    ):

        redirect = require_staff(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                f"/staff/team/events/{event_id}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        clean_action = str(
            action or ""
        ).strip().lower()

        if clean_action not in {
            "register",
            "withdraw",
        }:
            return RedirectResponse(
                f"/staff/team/events/{event_id}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        staff_email = str(
            request.session.get(
                "staff_email",
                "",
            )
        ).strip().lower()

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team/events",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:

            member = session.scalar(
                select(StaffMember).where(
                    StaffMember.email.ilike(
                        staff_email
                    )
                )
            )

            event = session.get(
                Event,
                event_id,
            )

            if (
                member is None
                or event is None
                or event.status == "archived"
            ):
                return RedirectResponse(
                    "/staff/team/events",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            row = session.scalar(
                select(StaffEventInterest).where(
                    StaffEventInterest.event_id
                    == event.id,
                    StaffEventInterest.staff_member_id
                    == member.id,
                )
            )

            if row is None:
                row = StaffEventInterest(
                    event_id=event.id,
                    staff_member_id=member.id,
                    status=(
                        "interested"
                        if clean_action == "register"
                        else "withdrawn"
                    ),
                )

                session.add(row)

            else:
                row.status = (
                    "interested"
                    if clean_action == "register"
                    else "withdrawn"
                )

            session.commit()

        return RedirectResponse(
            f"/staff/team/events/{event_id}?interest=saved",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.get(
        "/my-work",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def my_work(request: Request):
        redirect = require_staff(request)

        if redirect:
            return redirect

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        member = None
        event_rows = []
        completed = []
        approved_minutes = 0
        additional_work = []
        additional_work_events = []
        additional_work_minutes = 0

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is not None:
            with session_factory() as session:
                member = session.scalar(
                    select(StaffMember).where(
                        StaffMember.email.ilike(staff_email)
                    )
                )

                if member is not None:
                    additional_rows = session.execute(
                        select(
                            StaffAdditionalWork,
                            Event,
                        )
                        .outerjoin(
                            Event,
                            Event.id
                            == StaffAdditionalWork.event_id,
                        )
                        .where(
                            StaffAdditionalWork.staff_member_id
                            == member.id
                        )
                        .order_by(
                            StaffAdditionalWork.work_date.desc(),
                            StaffAdditionalWork.created_at.desc(),
                        )
                    ).all()

                    for work, linked_event in additional_rows:
                        minutes = max(
                            (
                                work.finish_time.hour * 60
                                + work.finish_time.minute
                            )
                            - (
                                work.start_time.hour * 60
                                + work.start_time.minute
                            )
                            - max(
                                int(work.break_minutes or 0),
                                0,
                            ),
                            0,
                        )

                        additional_work.append(
                            {
                                "work": work,
                                "event": linked_event,
                                "worked_minutes": minutes,
                            }
                        )

                        additional_work_minutes += minutes

                    additional_work_events = list(
                        session.scalars(
                            select(Event)
                            .where(
                                Event.status != "archived"
                            )
                            .order_by(
                                Event.start_date.desc(),
                                Event.name.asc(),
                            )
                        )
                    )

                    assignments = session.execute(
                        select(
                            EventStaffAssignment,
                            Event,
                        )
                        .join(
                            Event,
                            Event.id
                            == EventStaffAssignment.event_id,
                        )
                        .where(
                            EventStaffAssignment.staff_member_id
                            == member.id,
                            EventStaffAssignment.assignment_status
                            == "assigned",
                        )
                        .order_by(
                            Event.start_date.asc(),
                            Event.name.asc(),
                        )
                    ).all()

                    availability_rows = list(
                        session.scalars(
                            select(EventAvailabilityRequest).where(
                                EventAvailabilityRequest.staff_source_ref
                                == member.staff_source_ref,
                            )
                        )
                    )

                    availability_by_event = {
                        str(row.event_source_ref or "").strip(): row
                        for row in availability_rows
                        if str(row.event_source_ref or "").strip()
                    }

                    for assignment, event in assignments:
                        event_ref = str(
                            event.source_ref or ""
                        ).strip()

                        availability_record = (
                            availability_by_event.get(event_ref)
                        )

                        availability_status = (
                            str(
                                availability_record.availability_status
                                if availability_record is not None
                                else "unknown"
                            )
                            .strip()
                            .lower()
                        )

                        shifts = list(
                            session.scalars(
                                select(EventStaffShift)
                                .where(
                                    EventStaffShift.assignment_id
                                    == assignment.id
                                )
                                .order_by(
                                    EventStaffShift.shift_date.asc()
                                )
                            )
                        )

                        shift_map = {
                            shift.shift_date: shift
                            for shift in shifts
                        }

                        days = []
                        current_day = event.start_date

                        while current_day <= event.end_date:
                            shift = shift_map.get(current_day)
                            worked_minutes = None

                            if (
                                shift is not None
                                and shift.actual_clock_in is not None
                                and shift.actual_clock_out is not None
                            ):
                                seconds = (
                                    shift.actual_clock_out
                                    - shift.actual_clock_in
                                ).total_seconds()

                                worked_minutes = max(
                                    0,
                                    int(seconds // 60)
                                    - int(shift.break_minutes or 0),
                                )

                            days.append(
                                {
                                    "date": current_day,
                                    "shift": shift,
                                    "worked_minutes": worked_minutes,
                                }
                            )

                            if (
                                shift is not None
                                and shift.status == "complete"
                            ):
                                completed.append(
                                    {
                                        "shift": shift,
                                        "event": event,
                                        "assignment": assignment,
                                        "worked_minutes": worked_minutes,
                                    }
                                )

                                if (
                                    shift.approved
                                    and worked_minutes is not None
                                ):
                                    approved_minutes += worked_minutes

                            current_day += timedelta(days=1)

                        event_total_minutes = sum(
                            int(day["worked_minutes"] or 0)
                            for day in days
                            if day["worked_minutes"] is not None
                        )

                        event_rows.append(
                            {
                                "event": event,
                                "assignment": assignment,
                                "days": days,
                                "event_total_minutes":
                                    event_total_minutes,
                                "availability_status":
                                    availability_status,
                            }
                        )

        # --------------------------------------------------
        # STAFF MY WORK EVENT STATE
        # --------------------------------------------------
        #
        # Finished events disappear from My Work once every
        # working day has completed hours.
        #
        # A finished event with outstanding hours remains
        # visible, but availability is no longer relevant.
        # Only the days still requiring hours are presented.

        staff_today = date.today()
        visible_event_rows = []

        for event_row in event_rows:

            event = event_row.get("event")
            days = list(
                event_row.get("days")
                or []
            )

            event_finished = bool(
                event is not None
                and event.end_date is not None
                and event.end_date < staff_today
            )

            missing_days = []

            for day_row in days:

                shift = day_row.get("shift")

                shift_status = (
                    str(
                        shift.status or ""
                    ).lower()
                    if shift is not None
                    else ""
                )

                if (
                    shift is None
                    or shift_status not in (
                        "complete",
                        "did_not_work",
                    )
                ):
                    missing_days.append(
                        day_row
                    )

            event_row[
                "event_finished"
            ] = event_finished

            event_row[
                "missing_days"
            ] = missing_days

            event_row[
                "missing_hours_count"
            ] = len(missing_days)

            event_row[
                "hours_complete"
            ] = len(missing_days) == 0

            # Completed historical events belong in
            # My Timesheets, not the active My Work page.
            if (
                event_finished
                and not missing_days
            ):
                continue

            visible_event_rows.append(
                event_row
            )

        event_rows = visible_event_rows

        context = common_context(request)

        context.update(
            {
                "active_nav": "my_work",
                "member": member,
                "event_rows": event_rows,
                "completed": completed,
                "approved_minutes": approved_minutes,
                "additional_work": additional_work,
                "additional_work_events": additional_work_events,
                "additional_work_minutes": additional_work_minutes,
                "csrf_token": team_csrf(request),
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="my_work.html",
            context=context,
        )


    @router.post(
        "/my-work/additional-work",
        include_in_schema=False,
    )
    async def my_work_additional_work(
        request: Request,
        work_date: str = Form(...),
        work_type: str = Form(...),
        description: str = Form(...),
        start_time: str = Form(...),
        finish_time: str = Form(...),
        break_minutes: int = Form(0),
        event_id: str = Form(""),
        csrf_token: str = Form(...),
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        target = "/staff/team/my-work"

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        staff_email = str(
            request.session.get(
                "staff_email",
                "",
            )
        ).strip().lower()

        try:
            parsed_date = date.fromisoformat(
                str(work_date or "").strip()
            )

            parsed_start = dt_time.fromisoformat(
                str(start_time or "").strip()
            )

            parsed_finish = dt_time.fromisoformat(
                str(finish_time or "").strip()
            )

            clean_break = max(
                int(break_minutes or 0),
                0,
            )

        except (ValueError, TypeError):
            return RedirectResponse(
                target + "?additional_work=invalid",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        if parsed_finish <= parsed_start:
            return RedirectResponse(
                target + "?additional_work=invalid_time",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        clean_type = str(
            work_type or ""
        ).strip()[:160]

        clean_description = str(
            description or ""
        ).strip()[:4000]

        clean_event_id = str(
            event_id or ""
        ).strip()

        if not clean_type or not clean_description:
            return RedirectResponse(
                target + "?additional_work=missing",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            member = session.scalar(
                select(StaffMember).where(
                    StaffMember.email.ilike(
                        staff_email
                    )
                )
            )

            if member is None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            linked_event_id = None

            if clean_event_id:
                linked_event = session.get(
                    Event,
                    clean_event_id,
                )

                if linked_event is None:
                    return RedirectResponse(
                        target + "?additional_work=bad_event",
                        status_code=status.HTTP_303_SEE_OTHER,
                    )

                linked_event_id = linked_event.id

            row = StaffAdditionalWork(
                staff_member_id=member.id,
                event_id=linked_event_id,
                work_date=parsed_date,
                work_type=clean_type,
                description=clean_description,
                start_time=parsed_start,
                finish_time=parsed_finish,
                break_minutes=clean_break,
                status="submitted",
                approved=False,
            )

            session.add(row)
            session.commit()

        return RedirectResponse(
            target + "?additional_work=saved",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.post(
        "/my-work/availability",
        include_in_schema=False,
    )
    async def my_work_availability(
        request: Request,
        event_id: str = Form(...),
        availability_status: str = Form(...),
        csrf_token: str = Form(...),
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                "/staff/team/my-work",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        choice = str(
            availability_status or ""
        ).strip().lower()

        if choice not in {
            "available",
            "unavailable",
        }:
            return RedirectResponse(
                "/staff/team/my-work",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team/my-work",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            member = session.scalar(
                select(StaffMember).where(
                    StaffMember.email.ilike(staff_email)
                )
            )

            event = session.get(Event, event_id)

            if member is None or event is None:
                return RedirectResponse(
                    "/staff/team/my-work",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            assignment = session.scalar(
                select(EventStaffAssignment).where(
                    EventStaffAssignment.event_id == event.id,
                    EventStaffAssignment.staff_member_id == member.id,
                    EventStaffAssignment.assignment_status == "assigned",
                )
            )

            if assignment is None:
                return RedirectResponse(
                    "/staff/team/my-work",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            event_ref = str(
                event.source_ref or ""
            ).strip()

            staff_ref = str(
                member.staff_source_ref or ""
            ).strip()

            if not event_ref or not staff_ref:
                return RedirectResponse(
                    "/staff/team/my-work",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            availability = session.scalar(
                select(EventAvailabilityRequest)
                .where(
                    EventAvailabilityRequest.event_source_ref
                    == event_ref,
                    EventAvailabilityRequest.staff_source_ref
                    == staff_ref,
                )
                .order_by(
                    EventAvailabilityRequest.updated_at.desc()
                )
            )

            now = datetime.now(timezone.utc)

            if availability is None:
                availability = EventAvailabilityRequest(
                    source_ref=(
                        "staff-self-service:"
                        + event_ref
                        + ":"
                        + staff_ref
                    ),
                    event_source_ref=event_ref,
                    event_name=str(event.name or ""),
                    venue=str(event.venue or ""),
                    start_date=event.start_date,
                    end_date=event.end_date,
                    lock_date=event.start_date,
                    staff_source_ref=staff_ref,
                    staff_name=str(
                        member.preferred_name
                        or member.staff_name
                        or ""
                    ),
                    staff_email=str(
                        member.email or staff_email
                    ),
                    status="submitted",
                    availability_status=choice,
                    response_notes="",
                    submitted_at=now,
                    expires_at=now + timedelta(days=365),
                )

                session.add(availability)

            else:
                availability.status = "submitted"
                availability.availability_status = choice
                availability.submitted_at = now

            session.commit()

        return RedirectResponse(
            "/staff/team/my-work?availability=saved",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.post(
        "/my-work/did-not-work",
        include_in_schema=False,
    )
    async def my_work_did_not_work(
        request: Request,
        event_id: str = Form(...),
        work_date: str = Form(...),
        action: str = Form("mark"),
        csrf_token: str = Form(...),
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        target = "/staff/team/my-work"

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        staff_email = str(
            request.session.get(
                "staff_email",
                "",
            )
            or ""
        ).strip().lower()

        if not staff_email:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        try:
            parsed_date = date.fromisoformat(
                work_date.strip()
            )
        except ValueError:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:

            member = session.scalar(
                select(StaffMember).where(
                    StaffMember.email.ilike(
                        staff_email
                    )
                )
            )

            if member is None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            event = session.get(
                Event,
                event_id,
            )

            if event is None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            assignment = session.scalar(
                select(EventStaffAssignment).where(
                    EventStaffAssignment.event_id
                    == event.id,
                    EventStaffAssignment.staff_member_id
                    == member.id,
                    EventStaffAssignment.assignment_status
                    == "assigned",
                )
            )

            if assignment is None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            if not (
                event.start_date
                <= parsed_date
                <= event.end_date
            ):
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            shift = session.scalar(
                select(EventStaffShift).where(
                    EventStaffShift.assignment_id
                    == assignment.id,
                    EventStaffShift.shift_date
                    == parsed_date,
                )
            )

            clean_action = str(
                action or "mark"
            ).strip().lower()

            if clean_action == "undo":

                if (
                    shift is not None
                    and str(
                        shift.status or ""
                    ).lower()
                    == "did_not_work"
                ):
                    shift.status = "scheduled"

            else:

                # Some event assignments do not have a
                # physical shift row until hours are entered.
                # Create the operational day here so
                # Did Not Work can still be recorded.
                if shift is None:
                    shift = EventStaffShift(
                        event_id=event.id,
                        assignment_id=assignment.id,
                        staff_member_id=member.id,
                        shift_date=parsed_date,
                        role=assignment.role,
                        status="scheduled",
                    )

                    session.add(shift)

                # A did-not-work day is deliberately
                # non-payable and carries no attendance
                # hours into payroll.
                shift.status = "did_not_work"
                shift.actual_clock_in = None
                shift.actual_clock_out = None
                shift.break_minutes = 0
                shift.approved = False
                shift.approved_at = None
                shift.approved_by = ""

            session.commit()

        return RedirectResponse(
            target,
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.post(
        "/my-work/hours",
        include_in_schema=False,
    )
    async def my_work_save_hours(
        request: Request,
        event_id: str = Form(...),
        work_date: str = Form(...),
        start_time: str = Form(...),
        finish_time: str = Form(...),
        break_minutes: int = Form(0),
        csrf_token: str = Form(...),
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                "/staff/team/my-work",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        try:
            parsed_date = date.fromisoformat(
                work_date.strip()
            )

            parsed_start = dt_time.fromisoformat(
                start_time.strip()
            )

            parsed_finish = dt_time.fromisoformat(
                finish_time.strip()
            )

            clean_break = max(
                int(break_minutes or 0),
                0,
            )

        except (ValueError, TypeError):
            return RedirectResponse(
                "/staff/team/my-work",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        if parsed_finish <= parsed_start:
            return RedirectResponse(
                "/staff/team/my-work",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team/my-work",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            member = session.scalar(
                select(StaffMember).where(
                    StaffMember.email.ilike(
                        staff_email
                    )
                )
            )

            if member is None:
                return RedirectResponse(
                    "/staff/team/my-work",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            event = session.get(
                Event,
                event_id,
            )

            if event is None:
                return RedirectResponse(
                    "/staff/team/my-work",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            assignment = session.scalar(
                select(EventStaffAssignment).where(
                    EventStaffAssignment.event_id
                    == event.id,
                    EventStaffAssignment.staff_member_id
                    == member.id,
                    EventStaffAssignment.assignment_status
                    == "assigned",
                )
            )

            if assignment is None:
                return RedirectResponse(
                    "/staff/team/my-work",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            # The submitted work date must belong to this event.
            if not (
                event.start_date
                <= parsed_date
                <= event.end_date
            ):
                return RedirectResponse(
                    "/staff/team/my-work",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            # Staff can only enter hours after positively
            # confirming availability for this event.
            availability = session.scalar(
                select(EventAvailabilityRequest)
                .where(
                    EventAvailabilityRequest.event_source_ref
                    == event.source_ref,
                    EventAvailabilityRequest.staff_source_ref
                    == member.staff_source_ref,
                    EventAvailabilityRequest.status
                    == "submitted",
                    EventAvailabilityRequest.availability_status
                    == "available",
                )
                .order_by(
                    EventAvailabilityRequest.submitted_at.desc()
                )
            )

            if availability is None:
                return RedirectResponse(
                    "/staff/team/my-work",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            shift = session.scalar(
                select(EventStaffShift).where(
                    EventStaffShift.assignment_id
                    == assignment.id,
                    EventStaffShift.shift_date
                    == parsed_date,
                )
            )

            if shift is None:
                shift = EventStaffShift(
                    event_id=event.id,
                    assignment_id=assignment.id,
                    staff_member_id=member.id,
                    shift_date=parsed_date,
                    role=assignment.role,
                )

                session.add(shift)

            london = ZoneInfo("Europe/London")

            local_start = datetime.combine(
                parsed_date,
                parsed_start,
                tzinfo=london,
            )

            local_finish = datetime.combine(
                parsed_date,
                parsed_finish,
                tzinfo=london,
            )

            shift.role = assignment.role
            shift.scheduled_start = parsed_start
            shift.scheduled_finish = parsed_finish

            shift.actual_clock_in = (
                local_start.astimezone(timezone.utc)
            )

            shift.actual_clock_out = (
                local_finish.astimezone(timezone.utc)
            )

            shift.break_minutes = clean_break
            shift.status = "complete"

            # Any staff edit requires fresh management approval.
            shift.approved = False
            shift.approved_by = ""
            shift.approved_at = None

            session.commit()

        return RedirectResponse(
            "/staff/team/my-work",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.get(
        "/approved-hours",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def approved_hours(request: Request):
        redirect = require_staff(request)

        if redirect:
            return redirect

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        rows = []
        payroll_groups = []
        total_minutes = 0

        if session_factory is not None:
            with session_factory() as session:
                result = session.execute(
                    select(
                        EventStaffShift,
                        StaffMember,
                        Event,
                    )
                    .join(
                        StaffMember,
                        StaffMember.id
                        == EventStaffShift.staff_member_id,
                    )
                    .join(
                        Event,
                        Event.id
                        == EventStaffShift.event_id,
                    )
                    .where(
                        EventStaffShift.approved.is_(True),
                        EventStaffShift.status == "complete",
                        EventStaffShift.actual_clock_in.is_not(None),
                        EventStaffShift.actual_clock_out.is_not(None),
                    )
                    .order_by(
                        StaffMember.staff_source_ref.asc(),
                        EventStaffShift.shift_date.asc(),
                        EventStaffShift.actual_clock_in.asc(),
                    )
                ).all()

                additional_result = session.execute(
                    select(
                        StaffAdditionalWork,
                        StaffMember,
                        Event,
                    )
                    .join(
                        StaffMember,
                        StaffMember.id
                        == StaffAdditionalWork.staff_member_id,
                    )
                    .outerjoin(
                        Event,
                        Event.id
                        == StaffAdditionalWork.event_id,
                    )
                    .where(
                        StaffAdditionalWork.approved.is_(True),
                        StaffAdditionalWork.status == "approved",
                    )
                    .order_by(
                        StaffMember.staff_source_ref.asc(),
                        StaffAdditionalWork.work_date.asc(),
                        StaffAdditionalWork.start_time.asc(),
                    )
                ).all()

                grouped = {}

                for shift, member, event in result:
                    elapsed = (
                        shift.actual_clock_out
                        - shift.actual_clock_in
                    )

                    worked_minutes = max(
                        int(elapsed.total_seconds() // 60)
                        - max(int(shift.break_minutes or 0), 0),
                        0,
                    )

                    # Pirouette Payroll weeks begin on Wednesday.
                    days_since_wednesday = (
                        shift.shift_date.weekday() - 2
                    ) % 7

                    week_start = (
                        shift.shift_date
                        - timedelta(
                            days=days_since_wednesday
                        )
                    )

                    week_end = (
                        week_start
                        + timedelta(days=6)
                    )

                    item = {
                        "shift": shift,
                        "member": member,
                        "event": event,
                        "worked_minutes": worked_minutes,
                        "week_start": week_start,
                        "week_end": week_end,
                    }

                    rows.append(item)
                    total_minutes += worked_minutes

                    key = (
                        member.staff_source_ref,
                        week_start,
                    )

                    group = grouped.setdefault(
                        key,
                        {
                            "member": member,
                            "week_start": week_start,
                            "week_end": week_end,
                            "rows": [],
                            "worked_minutes": 0,
                            "work_dates": {},
                        },
                    )

                    group["rows"].append(item)
                    group["worked_minutes"] += worked_minutes

                    work_date = shift.shift_date.isoformat()

                    start_minutes = (
                        shift.actual_clock_in.hour * 60
                        + shift.actual_clock_in.minute
                    )

                    finish_minutes = (
                        shift.actual_clock_out.hour * 60
                        + shift.actual_clock_out.minute
                    )

                    if finish_minutes < start_minutes:
                        finish_minutes += 24 * 60

                    group["work_dates"].setdefault(
                        work_date,
                        [],
                    ).append(
                        {
                            "source_type": "event_shift",
                            "source_id": shift.id,
                            "start_minutes": start_minutes,
                            "finish_minutes": finish_minutes,
                            "worked_minutes": worked_minutes,
                        }
                    )


                # --------------------------------------------------
                # Approved Additional Work joins the same staff/week
                # grouping but remains a separate audit record.
                # --------------------------------------------------

                for work, member, linked_event in additional_result:

                    start_minutes = (
                        work.start_time.hour * 60
                        + work.start_time.minute
                    )

                    finish_minutes = (
                        work.finish_time.hour * 60
                        + work.finish_time.minute
                    )

                    if finish_minutes < start_minutes:
                        finish_minutes += 24 * 60

                    worked_minutes = max(
                        finish_minutes
                        - start_minutes
                        - max(
                            int(work.break_minutes or 0),
                            0,
                        ),
                        0,
                    )

                    days_since_wednesday = (
                        work.work_date.weekday() - 2
                    ) % 7

                    week_start = (
                        work.work_date
                        - timedelta(
                            days=days_since_wednesday
                        )
                    )

                    week_end = (
                        week_start
                        + timedelta(days=6)
                    )

                    # Additional Work is already visible in
                    # Timesheet Approvals. Here it contributes to
                    # the Approved Hours weekly/payroll total.
                    total_minutes += worked_minutes

                    key = (
                        member.staff_source_ref,
                        week_start,
                    )

                    group = grouped.setdefault(
                        key,
                        {
                            "member": member,
                            "week_start": week_start,
                            "week_end": week_end,
                            "rows": [],
                            "worked_minutes": 0,
                            "work_dates": {},
                        },
                    )

                    group["worked_minutes"] += worked_minutes

                    work_date = work.work_date.isoformat()

                    group["work_dates"].setdefault(
                        work_date,
                        [],
                    ).append(
                        {
                            "source_type": "additional_work",
                            "source_id": work.id,
                            "start_minutes": start_minutes,
                            "finish_minutes": finish_minutes,
                            "worked_minutes": worked_minutes,
                        }
                    )

                for (
                    staff_source_ref,
                    week_start,
                ), group in grouped.items():
                    payroll_day_conflicts = []

                    for (
                        work_date,
                        day_segments,
                    ) in group["work_dates"].items():

                        if not day_segments:
                            continue

                        earliest = min(
                            int(
                                segment["start_minutes"]
                            )
                            for segment in day_segments
                        )

                        latest = max(
                            int(
                                segment["finish_minutes"]
                            )
                            for segment in day_segments
                        )

                        paid_minutes = sum(
                            int(
                                segment["worked_minutes"]
                                or 0
                            )
                            for segment in day_segments
                        )

                        available_span = max(
                            latest - earliest,
                            0,
                        )

                        if paid_minutes > available_span:
                            payroll_day_conflicts.append(
                                work_date
                            )

                    # Keep the existing template variable name
                    # for backwards compatibility. It now means
                    # a genuine payroll-day conflict rather than
                    # merely more than one job on the same date.
                    duplicate_dates = payroll_day_conflicts

                    safe_staff_ref = (
                        str(staff_source_ref or "")
                        .strip()
                        .replace(" ", "-")
                    )

                    source_ref = (
                        "STUPHIE-PAYROLL-"
                        f"{safe_staff_ref}-"
                        f"{week_start.isoformat()}"
                    )

                    group["source_ref"] = source_ref[:220]
                    group["duplicate_dates"] = duplicate_dates

                    existing_payroll = session.scalar(
                        select(StaffPayrollSubmission).where(
                            StaffPayrollSubmission.source_ref
                            == group["source_ref"]
                        )
                    )

                    group["existing_payroll_id"] = (
                        existing_payroll.id
                        if existing_payroll is not None
                        else ""
                    )

                    group["existing_payroll_status"] = (
                        str(existing_payroll.status or "")
                        if existing_payroll is not None
                        else ""
                    )

                    group["ready"] = (
                        not duplicate_dates
                        and existing_payroll is None
                    )

                    payroll_groups.append(group)

        context = common_context(request)

        context.update(
            {
                "active_nav": "approved_hours",
                "rows": rows,
                "payroll_groups": payroll_groups,
                "total_minutes": total_minutes,
                "csrf_token": team_csrf(request),
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="approved_hours.html",
            context=context,
        )


    @router.post(
        "/approved-hours/create-draft",
        include_in_schema=False,
    )
    async def approved_hours_create_draft(
        request: Request,
        staff_source_ref: str = Form(...),
        week_start: str = Form(...),
        csrf_token: str = Form(...),
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        target = "/staff/team/approved-hours"

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        clean_staff_ref = str(
            staff_source_ref or ""
        ).strip()

        if not clean_staff_ref:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        try:
            parsed_week_start = datetime.strptime(
                str(week_start or "").strip(),
                "%Y-%m-%d",
            ).date()
        except ValueError:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # Pirouette Payroll weeks always start Wednesday.
        if parsed_week_start.weekday() != 2:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        week_end = (
            parsed_week_start
            + timedelta(days=6)
        )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        safe_staff_ref = (
            clean_staff_ref
            .replace(" ", "-")
        )

        source_ref = (
            "STUPHIE-PAYROLL-"
            f"{safe_staff_ref}-"
            f"{parsed_week_start.isoformat()}"
        )[:220]

        with session_factory() as session:
            # Deterministic source_ref makes the handoff
            # idempotent. Never create a duplicate week.
            existing = session.scalar(
                select(StaffPayrollSubmission).where(
                    StaffPayrollSubmission.source_ref
                    == source_ref
                )
            )

            if existing is not None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            member = session.scalar(
                select(StaffMember).where(
                    StaffMember.staff_source_ref
                    == clean_staff_ref
                )
            )

            if member is None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            # --------------------------------------------------
            # PRIVATE PAYMENT SETUP
            # --------------------------------------------------
            #
            # Pay arrangement and rates are Payroll Admin data.
            # They are never accepted from the Approved Hours
            # browser form and are never exposed to staff.
            #
            # The applicable rate is the latest effective rate
            # on or before this payroll week's start date. This
            # preserves historic payroll calculations when a
            # future pay rate is introduced.

            payment_profile = session.scalar(
                select(StaffPaymentProfile).where(
                    StaffPaymentProfile.staff_member_id
                    == member.id
                )
            )

            payment_arrangement = (
                str(
                    payment_profile.payment_arrangement
                    if payment_profile is not None
                    else "self_employed_contractor"
                )
                .strip()
                or "self_employed_contractor"
            )

            applicable_rate = session.scalar(
                select(StaffPayRate)
                .where(
                    StaffPayRate.staff_member_id
                    == member.id,
                    StaffPayRate.effective_from
                    <= parsed_week_start,
                )
                .order_by(
                    StaffPayRate.effective_from.desc(),
                    StaffPayRate.created_at.desc(),
                )
                .limit(1)
            )

            # Contractors require a private rate before payroll
            # can be handed off. External-payroll staff are
            # recorded for hours reporting but contractor pay is
            # deliberately not calculated by this workflow.
            if (
                payment_arrangement
                == "self_employed_contractor"
                and applicable_rate is None
            ):
                return RedirectResponse(
                    target + "?payroll_error=missing_rate",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            if (
                payment_arrangement
                == "self_employed_contractor"
            ):
                parsed_hourly_rate = round(
                    applicable_rate.hourly_rate_pence / 100,
                    2,
                )
                rate_effective_from = (
                    applicable_rate.effective_from.isoformat()
                )
            else:
                parsed_hourly_rate = 0.0
                rate_effective_from = ""

            event_result = session.execute(
                select(
                    EventStaffShift,
                    Event,
                )
                .join(
                    Event,
                    Event.id
                    == EventStaffShift.event_id,
                )
                .where(
                    EventStaffShift.staff_member_id
                    == member.id,
                    EventStaffShift.approved.is_(True),
                    EventStaffShift.status == "complete",
                    EventStaffShift.actual_clock_in.is_not(None),
                    EventStaffShift.actual_clock_out.is_not(None),
                    EventStaffShift.shift_date
                    >= parsed_week_start,
                    EventStaffShift.shift_date
                    <= week_end,
                )
                .order_by(
                    EventStaffShift.shift_date.asc(),
                    EventStaffShift.actual_clock_in.asc(),
                )
            ).all()

            additional_result = session.execute(
                select(
                    StaffAdditionalWork,
                    Event,
                )
                .outerjoin(
                    Event,
                    Event.id
                    == StaffAdditionalWork.event_id,
                )
                .where(
                    StaffAdditionalWork.staff_member_id
                    == member.id,
                    StaffAdditionalWork.approved.is_(True),
                    StaffAdditionalWork.status
                    == "approved",
                    StaffAdditionalWork.work_date
                    >= parsed_week_start,
                    StaffAdditionalWork.work_date
                    <= week_end,
                )
                .order_by(
                    StaffAdditionalWork.work_date.asc(),
                    StaffAdditionalWork.start_time.asc(),
                )
            ).all()

            if (
                not event_result
                and not additional_result
            ):
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )


            # --------------------------------------------------
            # BUILD SOURCE SEGMENTS
            # --------------------------------------------------

            day_segments = {}
            approved_source_records = []


            for shift, event in event_result:

                work_date = (
                    shift.shift_date.isoformat()
                )

                start_minutes = (
                    shift.actual_clock_in.hour * 60
                    + shift.actual_clock_in.minute
                )

                finish_minutes = (
                    shift.actual_clock_out.hour * 60
                    + shift.actual_clock_out.minute
                )

                if finish_minutes < start_minutes:
                    finish_minutes += 24 * 60

                break_minutes = max(
                    int(
                        shift.break_minutes or 0
                    ),
                    0,
                )

                paid_minutes = max(
                    finish_minutes
                    - start_minutes
                    - break_minutes,
                    0,
                )

                notes_parts = [
                    str(event.name or "").strip(),
                ]

                shift_notes = str(
                    shift.notes or ""
                ).strip()

                if shift_notes:
                    notes_parts.append(
                        shift_notes
                    )

                notes = " - ".join(
                    part
                    for part in notes_parts
                    if part
                )

                segment = {
                    "source_type": "event_shift",
                    "source_id": shift.id,
                    "event_id": event.id,
                    "event_name": str(
                        event.name or ""
                    ),
                    "work_date": work_date,
                    "start_time":
                        shift.actual_clock_in.strftime(
                            "%H:%M"
                        ),
                    "finish_time":
                        shift.actual_clock_out.strftime(
                            "%H:%M"
                        ),
                    "break_minutes":
                        break_minutes,
                    "paid_minutes":
                        paid_minutes,
                    "notes": notes,
                }

                day_segments.setdefault(
                    work_date,
                    [],
                ).append(
                    {
                        **segment,
                        "_start_minutes":
                            start_minutes,
                        "_finish_minutes":
                            finish_minutes,
                    }
                )

                approved_source_records.append(
                    segment
                )


            for work, linked_event in additional_result:

                work_date = (
                    work.work_date.isoformat()
                )

                start_minutes = (
                    work.start_time.hour * 60
                    + work.start_time.minute
                )

                finish_minutes = (
                    work.finish_time.hour * 60
                    + work.finish_time.minute
                )

                if finish_minutes < start_minutes:
                    finish_minutes += 24 * 60

                break_minutes = max(
                    int(
                        work.break_minutes or 0
                    ),
                    0,
                )

                paid_minutes = max(
                    finish_minutes
                    - start_minutes
                    - break_minutes,
                    0,
                )

                notes_parts = []

                if linked_event is not None:
                    notes_parts.append(
                        str(
                            linked_event.name
                            or ""
                        ).strip()
                    )

                notes_parts.append(
                    str(
                        work.work_type
                        or "Additional Work"
                    ).strip()
                )

                description = str(
                    work.description or ""
                ).strip()

                if description:
                    notes_parts.append(
                        description
                    )

                notes = " - ".join(
                    part
                    for part in notes_parts
                    if part
                )

                segment = {
                    "source_type":
                        "additional_work",
                    "source_id": work.id,
                    "event_id": (
                        linked_event.id
                        if linked_event
                        else None
                    ),
                    "event_name": (
                        str(
                            linked_event.name
                            or ""
                        )
                        if linked_event
                        else ""
                    ),
                    "work_type": str(
                        work.work_type
                        or "Additional Work"
                    ),
                    "description":
                        description,
                    "work_date":
                        work_date,
                    "start_time":
                        work.start_time.strftime(
                            "%H:%M"
                        ),
                    "finish_time":
                        work.finish_time.strftime(
                            "%H:%M"
                        ),
                    "break_minutes":
                        break_minutes,
                    "paid_minutes":
                        paid_minutes,
                    "notes":
                        notes,
                }

                day_segments.setdefault(
                    work_date,
                    [],
                ).append(
                    {
                        **segment,
                        "_start_minutes":
                            start_minutes,
                        "_finish_minutes":
                            finish_minutes,
                    }
                )

                approved_source_records.append(
                    segment
                )


            # --------------------------------------------------
            # COLLAPSE EACH CALENDAR DAY TO ONE PAYROLL ROW
            #
            # Example:
            # 09:00-12:00 event
            # 14:00-16:00 editing
            #
            # becomes:
            # 09:00-16:00
            # 120 minute derived break
            # 5 paid hours
            # --------------------------------------------------

            timesheet_entries = []

            for work_date in sorted(
                day_segments
            ):

                segments = day_segments[
                    work_date
                ]

                earliest = min(
                    int(
                        segment[
                            "_start_minutes"
                        ]
                    )
                    for segment in segments
                )

                latest = max(
                    int(
                        segment[
                            "_finish_minutes"
                        ]
                    )
                    for segment in segments
                )

                paid_minutes = sum(
                    int(
                        segment[
                            "paid_minutes"
                        ]
                        or 0
                    )
                    for segment in segments
                )

                span_minutes = max(
                    latest - earliest,
                    0,
                )

                # This means source records overlap or
                # otherwise claim more paid time than
                # can physically fit into the day span.
                if paid_minutes > span_minutes:
                    return RedirectResponse(
                        target
                        + "?payroll=day_conflict",
                        status_code=(
                            status.HTTP_303_SEE_OTHER
                        ),
                    )

                derived_break = max(
                    span_minutes
                    - paid_minutes,
                    0,
                )

                start_clock = earliest % (
                    24 * 60
                )

                finish_clock = latest % (
                    24 * 60
                )

                start_value = (
                    f"{start_clock // 60:02d}:"
                    f"{start_clock % 60:02d}"
                )

                finish_value = (
                    f"{finish_clock // 60:02d}:"
                    f"{finish_clock % 60:02d}"
                )

                notes = " | ".join(
                    str(
                        segment.get(
                            "notes",
                            "",
                        )
                        or ""
                    ).strip()
                    for segment in segments
                    if str(
                        segment.get(
                            "notes",
                            "",
                        )
                        or ""
                    ).strip()
                )

                timesheet_entries.append(
                    {
                        "work_date":
                            work_date,
                        "start_time":
                            start_value,
                        "finish_time":
                            finish_value,
                        "break_minutes":
                            derived_break,
                        "notes":
                            notes,
                    }
                )

            payload = {
                "timesheet_entries":
                    timesheet_entries,
                "expense_claims": [],
                "hourly_rate":
                    parsed_hourly_rate,
                "payment_arrangement":
                    payment_arrangement,
                "rate_effective_from":
                    rate_effective_from,
                "rate_source":
                    "private_staff_pay_rate",
                "submission_status": "draft",

                # Full management audit of the approved
                # operational records that generated the
                # payroll draft. This remains separate from
                # the employee-editable seven-day payroll
                # timesheet.
                "approved_source_records":
                    approved_source_records,
            }

            now = datetime.now(timezone.utc)

            payroll = StaffPayrollSubmission(
                source_ref=source_ref,
                staff_source_ref=clean_staff_ref,
                staff_name=str(
                    member.preferred_name
                    or member.staff_name
                    or ""
                )[:200],
                staff_email=str(
                    member.email or ""
                )[:320],
                week_start=parsed_week_start,
                status="draft",
                payload_json=json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                expires_at=(
                    now
                    + timedelta(days=21)
                ),
            )

            session.add(payroll)
            session.commit()

        return RedirectResponse(
            target,
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.post(
        "/approved-hours/release-payroll",
        include_in_schema=False,
    )
    async def approved_hours_release_payroll(
        request: Request,
        payroll_id: str = Form(...),
        csrf_token: str = Form(...),
    ):
        redirect = require_staff(request)

        if redirect:
            return redirect

        target = "/staff/team/approved-hours"

        # Payroll release is a privileged management action.
        #
        # Reuse the same authorization conditions as Payroll Admin:
        #   1. authenticated Super Admin
        #   2. email explicitly authorised for Payroll Admin
        #   3. recent successful Payroll OTP verification
        #
        # This protects the POST route itself; hiding the button alone
        # would not be sufficient security.
        staff_role = str(
            request.session.get("staff_role") or ""
        ).strip()

        staff_email = str(
            request.session.get("staff_email") or ""
        ).strip().lower()

        settings = request.app.state.settings

        if (
            staff_role != "super_admin"
            or not staff_email
            or staff_email
            not in settings.payroll_otp_allowed_email_set
        ):
            raise HTTPException(
                403,
                "Payroll Admin authorization required.",
            )

        now_ts = time.time()

        verified_at = float(
            request.session.get(
                "payroll_otp_verified_at"
            )
            or 0
        )

        last_activity = float(
            request.session.get(
                "payroll_otp_last_activity"
            )
            or 0
        )

        unlock_seconds = int(
            settings.payroll_otp_unlock_seconds
        )

        if (
            verified_at <= 0
            or last_activity <= 0
            or now_ts - last_activity > unlock_seconds
        ):
            return RedirectResponse(
                "/staff/payroll/unlock",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        request.session[
            "payroll_otp_last_activity"
        ] = now_ts

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        clean_payroll_id = str(
            payroll_id or ""
        ).strip()

        if not clean_payroll_id:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            payroll = session.get(
                StaffPayrollSubmission,
                clean_payroll_id,
            )

            if payroll is None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            # Controlled management handoff only.
            #
            # Never re-release a submitted payroll and never send
            # a duplicate email for a payroll already released.
            if (
                payroll.status == "submitted"
                or payroll.emailed_at is not None
                or payroll.status == "emailed"
            ):
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            issue_secure_link(
                session,
                request.app.state.settings,
                payroll,
            )

        return RedirectResponse(
            target,
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.get(
        "/timesheets",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def timesheets(request: Request):
        redirect = require_staff(request)
        if redirect:
            return redirect

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        rows = []
        additional_rows = []
        additional_totals = {
            "all": 0,
            "approved": 0,
            "unapproved": 0,
            "returned": 0,
            "worked_minutes": 0,
        }
        totals = {
            "all": 0,
            "scheduled": 0,
            "working": 0,
            "complete": 0,
            "approved": 0,
            "unapproved": 0,
            "worked_minutes": 0,
        }

        if session_factory is not None:
            with session_factory() as session:
                result = session.execute(
                    select(
                        EventStaffShift,
                        StaffMember,
                        Event,
                    )
                    .join(
                        StaffMember,
                        StaffMember.id
                        == EventStaffShift.staff_member_id,
                    )
                    .join(
                        Event,
                        Event.id == EventStaffShift.event_id,
                    )
                    .order_by(
                        EventStaffShift.shift_date.desc(),
                        EventStaffShift.created_at.desc(),
                    )
                ).all()

                additional_result = session.execute(
                    select(
                        StaffAdditionalWork,
                        StaffMember,
                        Event,
                    )
                    .join(
                        StaffMember,
                        StaffMember.id
                        == StaffAdditionalWork.staff_member_id,
                    )
                    .outerjoin(
                        Event,
                        Event.id
                        == StaffAdditionalWork.event_id,
                    )
                    .order_by(
                        StaffAdditionalWork.work_date.desc(),
                        StaffAdditionalWork.created_at.desc(),
                    )
                ).all()

                for work, work_member, linked_event in additional_result:
                    worked_minutes = max(
                        (
                            work.finish_time.hour * 60
                            + work.finish_time.minute
                        )
                        - (
                            work.start_time.hour * 60
                            + work.start_time.minute
                        )
                        - max(
                            int(work.break_minutes or 0),
                            0,
                        ),
                        0,
                    )

                    additional_rows.append(
                        {
                            "work": work,
                            "member": work_member,
                            "event": linked_event,
                            "worked_minutes": worked_minutes,
                        }
                    )

                    additional_totals["all"] += 1
                    additional_totals["worked_minutes"] += (
                        worked_minutes
                    )

                    if work.approved:
                        additional_totals["approved"] += 1

                    elif work.status == "returned":
                        additional_totals["returned"] += 1

                    else:
                        additional_totals["unapproved"] += 1

                for shift, member, event in result:
                    worked_minutes = None

                    if (
                        shift.actual_clock_in is not None
                        and shift.actual_clock_out is not None
                    ):
                        elapsed = (
                            shift.actual_clock_out
                            - shift.actual_clock_in
                        )
                        worked_minutes = max(
                            int(elapsed.total_seconds() // 60)
                            - max(int(shift.break_minutes or 0), 0),
                            0,
                        )

                    rows.append(
                        {
                            "shift": shift,
                            "member": member,
                            "event": event,
                            "worked_minutes": worked_minutes,
                        }
                    )

                    totals["all"] += 1

                    status_value = str(
                        shift.status or "scheduled"
                    ).lower()

                    if status_value in (
                        "scheduled",
                        "working",
                        "complete",
                    ):
                        totals[status_value] += 1

                    if shift.approved:
                        totals["approved"] += 1
                    elif status_value == "complete":
                        totals["unapproved"] += 1

                    if worked_minutes is not None:
                        totals["worked_minutes"] += worked_minutes

        # --------------------------------------------------
        # GROUPED TIMESHEET APPROVAL MANAGEMENT
        # --------------------------------------------------
        #
        # Presentation-only grouping for the management screen.
        # Existing shift/additional-work records, approval routes,
        # payroll handoff and audit behaviour remain unchanged.
        #
        # Active groups contain records requiring management
        # attention. Approved records are separated into an archive
        # so completed work no longer dominates the working queue.

        approval_groups = {}

        def approval_group(
            staff_ref,
            staff_name,
        ):
            key = str(
                staff_ref
                or staff_name
                or "unknown"
            )

            if key not in approval_groups:
                approval_groups[key] = {
                    "staff_ref": staff_ref,
                    "staff_name": (
                        staff_name
                        or "Staff member"
                    ),
                    "pending_shifts": [],
                    "pending_additional": [],
                    "approved_shifts": [],
                    "approved_additional": [],
                    "pending_count": 0,
                    "approved_count": 0,
                    "pending_minutes": 0,
                    "approved_minutes": 0,
                }

            return approval_groups[key]

        for row in rows:
            shift = row.get("shift")
            member = row.get("member")

            if shift is None:
                continue

            staff_ref = (
                getattr(
                    member,
                    "staff_source_ref",
                    None,
                )
                if member is not None
                else getattr(
                    shift,
                    "staff_source_ref",
                    None,
                )
            )

            staff_name = (
                getattr(
                    member,
                    "preferred_name",
                    None,
                )
                or getattr(
                    member,
                    "staff_name",
                    None,
                )
                if member is not None
                else getattr(
                    shift,
                    "staff_name",
                    None,
                )
            )

            group = approval_group(
                staff_ref,
                staff_name,
            )

            worked_minutes = (
                row.get("worked_minutes")
                or 0
            )

            if shift.approved:
                group[
                    "approved_shifts"
                ].append(row)
                group["approved_count"] += 1
                group[
                    "approved_minutes"
                ] += worked_minutes

            elif str(
                shift.status or ""
            ).lower() == "complete":
                group[
                    "pending_shifts"
                ].append(row)
                group["pending_count"] += 1
                group[
                    "pending_minutes"
                ] += worked_minutes

        for row in additional_rows:
            work = row.get("work")
            member = row.get("member")

            if work is None:
                continue

            staff_ref = (
                getattr(
                    member,
                    "staff_source_ref",
                    None,
                )
                if member is not None
                else getattr(
                    work,
                    "staff_source_ref",
                    None,
                )
            )

            staff_name = (
                getattr(
                    member,
                    "preferred_name",
                    None,
                )
                or getattr(
                    member,
                    "staff_name",
                    None,
                )
                if member is not None
                else getattr(
                    work,
                    "staff_name",
                    None,
                )
            )

            group = approval_group(
                staff_ref,
                staff_name,
            )

            work_minutes = int(
                row.get("worked_minutes")
                or row.get("minutes")
                or 0
            )

            if work.approved:
                group[
                    "approved_additional"
                ].append(row)
                group["approved_count"] += 1
                group[
                    "approved_minutes"
                ] += work_minutes

            else:
                work_status = str(
                    getattr(
                        work,
                        "status",
                        "",
                    )
                    or ""
                ).lower()

                if work_status not in (
                    "returned",
                    "draft",
                ):
                    group[
                        "pending_additional"
                    ].append(row)
                    group["pending_count"] += 1
                    group[
                        "pending_minutes"
                    ] += work_minutes

        pending_staff_groups = [
            group
            for group in approval_groups.values()
            if group["pending_count"] > 0
        ]

        approved_staff_groups = [
            group
            for group in approval_groups.values()
            if group["approved_count"] > 0
        ]

        pending_staff_groups.sort(
            key=lambda item: str(
                item["staff_name"]
            ).lower()
        )

        approved_staff_groups.sort(
            key=lambda item: str(
                item["staff_name"]
            ).lower()
        )

        approved_archive_count = sum(
            group["approved_count"]
            for group in approved_staff_groups
        )

        context = common_context(request)
        context.update(
            {
                "rows": rows,
                "totals": totals,
                "additional_rows": additional_rows,
                "additional_totals": additional_totals,
                "pending_staff_groups":
                    pending_staff_groups,
                "approved_staff_groups":
                    approved_staff_groups,
                "approved_archive_count":
                    approved_archive_count,
                "active_nav": "timesheets",
                "csrf_token": team_csrf(request),
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="timesheets.html",
            context=context,
        )


    @router.post(
        "/timesheets/{shift_id}/correct",
        include_in_schema=False,
    )
    async def timesheet_correct(
        request: Request,
        shift_id: str,
        csrf_token: str = Form(...),
        actual_clock_in: str = Form(...),
        actual_clock_out: str = Form(...),
        break_minutes: int = Form(0),
    ):
        redirect = require_staff(request)
        if redirect:
            return redirect

        if not valid_team_csrf(request, csrf_token):
            return RedirectResponse(
                "/staff/team/timesheets",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team/timesheets",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            shift = session.get(EventStaffShift, shift_id)

            if shift is None:
                return RedirectResponse(
                    "/staff/team/timesheets",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            if shift.status != "complete":
                return RedirectResponse(
                    "/staff/team/timesheets",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            if shift.approved:
                return RedirectResponse(
                    "/staff/team/timesheets",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            clean_in = actual_clock_in.strip()
            clean_out = actual_clock_out.strip()

            try:
                clock_in_time = datetime.strptime(
                    clean_in,
                    "%Y-%m-%dT%H:%M",
                )

                clock_out_time = datetime.strptime(
                    clean_out,
                    "%Y-%m-%dT%H:%M",
                )
            except ValueError:
                return RedirectResponse(
                    "/staff/team/timesheets",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            existing_in = shift.actual_clock_in
            existing_out = shift.actual_clock_out

            if existing_in is not None and existing_in.tzinfo is not None:
                clock_in_time = clock_in_time.replace(
                    tzinfo=existing_in.tzinfo
                )

            if existing_out is not None and existing_out.tzinfo is not None:
                clock_out_time = clock_out_time.replace(
                    tzinfo=existing_out.tzinfo
                )

            if clock_out_time < clock_in_time:
                return RedirectResponse(
                    "/staff/team/timesheets",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            shift.actual_clock_in = clock_in_time
            shift.actual_clock_out = clock_out_time
            shift.break_minutes = max(int(break_minutes), 0)

            shift.manually_corrected = True
            shift.corrected_by = str(
                request.session.get("staff_email", "")
            )
            shift.corrected_at = datetime.now(timezone.utc)

            session.commit()

        return RedirectResponse(
            "/staff/team/timesheets",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.post(
        "/timesheets/{shift_id}/approve",
        include_in_schema=False,
    )
    async def timesheet_approve(
        request: Request,
        shift_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = require_staff(request)
        if redirect:
            return redirect

        if not valid_team_csrf(request, csrf_token):
            return RedirectResponse(
                "/staff/team/timesheets",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team/timesheets",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            shift = session.get(EventStaffShift, shift_id)

            if shift is None:
                return RedirectResponse(
                    "/staff/team/timesheets",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            if (
                shift.status != "complete"
                or shift.actual_clock_in is None
                or shift.actual_clock_out is None
            ):
                return RedirectResponse(
                    "/staff/team/timesheets",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            shift.approved = True
            shift.approved_by = str(
                request.session.get("staff_email", "")
            )
            shift.approved_at = datetime.now(timezone.utc)

            session.commit()

        return RedirectResponse(
            "/staff/team/timesheets",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.post(
        "/timesheets/additional-work/{work_id}/approve",
        include_in_schema=False,
    )
    async def additional_work_approve(
        request: Request,
        work_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        target = "/staff/team/timesheets"

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            work = session.get(
                StaffAdditionalWork,
                str(work_id or "").strip(),
            )

            if work is None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            if work.approved:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            work.approved = True
            work.status = "approved"

            work.approved_by = str(
                request.session.get(
                    "staff_email",
                    "",
                )
            )[:320]

            work.approved_at = datetime.now(
                timezone.utc
            )

            work.returned_by = ""
            work.returned_at = None
            work.return_notes = ""

            session.commit()

        return RedirectResponse(
            target + "?additional_work=approved",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.post(
        "/timesheets/additional-work/{work_id}/return",
        include_in_schema=False,
    )
    async def additional_work_return(
        request: Request,
        work_id: str,
        return_notes: str = Form(...),
        csrf_token: str = Form(...),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        target = "/staff/team/timesheets"

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        clean_notes = str(
            return_notes or ""
        ).strip()[:4000]

        if not clean_notes:
            return RedirectResponse(
                target
                + "?additional_work=return_note_required",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                target,
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            work = session.get(
                StaffAdditionalWork,
                str(work_id or "").strip(),
            )

            if work is None:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            # Approved work is locked.
            if work.approved:
                return RedirectResponse(
                    target,
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            work.approved = False
            work.status = "returned"

            work.returned_by = str(
                request.session.get(
                    "staff_email",
                    "",
                )
            )[:320]

            work.returned_at = datetime.now(
                timezone.utc
            )

            work.return_notes = clean_notes

            work.approved_by = ""
            work.approved_at = None

            session.commit()

        return RedirectResponse(
            target + "?additional_work=returned",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.get(
        "/{person_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def team_detail(
        request: Request,
        person_id: str,
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team",
                status_code=303,
            )

        with session_factory() as session:
            member = session.get(StaffMember, person_id)

            if member is None:
                return RedirectResponse(
                    "/staff/team",
                    status_code=303,
                )

            assignment_rows = session.execute(
                select(EventStaffAssignment, Event)
                .join(
                    Event,
                    Event.id == EventStaffAssignment.event_id,
                )
                .where(
                    EventStaffAssignment.staff_member_id
                    == member.id
                )
                .order_by(
                    Event.start_date.desc(),
                )
            ).all()

            availability_rows = list(
                session.scalars(
                    select(EventAvailabilityRequest)
                    .where(
                        EventAvailabilityRequest.staff_source_ref
                        == member.staff_source_ref
                    )
                    .order_by(
                        EventAvailabilityRequest.start_date.desc(),
                    )
                    .limit(50)
                )
            )

            profile_request = session.scalar(
                select(StaffProfileRequest)
                .where(
                    StaffProfileRequest.source_ref
                    == member.staff_source_ref
                )
                .order_by(
                    StaffProfileRequest.updated_at.desc()
                )
            )

            context = common_context(request)
            context.update(
                {
                    "member": member,
                    "assignment_rows": assignment_rows,
                    "availability_rows": availability_rows,
                    "profile_request": profile_request,
                    "profile_complete": bool(
                        member.profile_completed_at
                        or (
                            profile_request is not None
                            and profile_request.status == "submitted"
                        )
                    ),
                    "show_onboarding_controls": bool(
                        not member.password_hash
                        and not member.profile_completed_at
                    ),
                    "csrf_token": team_csrf(request),
                }
            )

            return templates.TemplateResponse(
                request=request,
                name="team_detail.html",
                context=context,
            )

    @router.post(
        "/{person_id}/send-profile-request",
        include_in_schema=False,
    )
    async def team_send_profile_request(
        request: Request,
        person_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                f"/staff/team/{person_id}",
                status_code=303,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                f"/staff/team/{person_id}",
                status_code=303,
            )

        with session_factory() as session:

            member = session.get(
                StaffMember,
                person_id,
            )

            if member is None:
                return RedirectResponse(
                    "/staff/team",
                    status_code=303,
                )

            if (
                not member.active
                or not str(member.email or "").strip()
                or not str(
                    member.staff_source_ref or ""
                ).strip()
            ):
                return RedirectResponse(
                    f"/staff/team/{person_id}"
                    "?profile_request=invalid",
                    status_code=303,
                )

            try:
                create_staff_profile_request(
                    session,
                    request.app.state.settings,
                    source_ref=member.staff_source_ref,
                    staff_code=member.staff_code or "",
                    staff_name=member.staff_name or "",
                    preferred_name=member.preferred_name or "",
                    role=(
                        member.default_role
                        or "Photography Staff"
                    ),
                    staff_email=member.email,
                )

            except Exception:
                return RedirectResponse(
                    f"/staff/team/{person_id}"
                    "?profile_request=failed",
                    status_code=303,
                )

        return RedirectResponse(
            f"/staff/team/{person_id}"
            "?profile_request=sent",
            status_code=303,
        )


    @router.post(
        "/{person_id}/send-login-setup",
        include_in_schema=False,
    )
    async def team_send_login_setup(
        request: Request,
        person_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        if not valid_team_csrf(request, csrf_token):
            return RedirectResponse(
                f"/staff/team/{person_id}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            member = session.get(StaffMember, person_id)

            if (
                member is None
                or not member.active
                or not member.email.strip()
            ):
                return RedirectResponse(
                    f"/staff/team/{person_id}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            raw_token = secrets.token_urlsafe(48)
            digest = hashlib.sha256(
                raw_token.encode("utf-8")
            ).hexdigest()

            # Initial-login email is onboarding only.
            # Existing staff accounts must use the normal password
            # recovery journey rather than being re-onboarded.
            if member.password_hash:
                return RedirectResponse(
                    f"/staff/team/{person_id}"
                    "?login_setup=already_complete",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            now = datetime.now(timezone.utc)

            from sqlalchemy import update

            session.execute(
                update(StaffPasswordReset)
                .where(
                    StaffPasswordReset.staff_id == member.id,
                    StaffPasswordReset.used_at.is_(None),
                )
                .values(used_at=now)
            )

            session.add(
                StaffPasswordReset(
                    staff_id=member.id,
                    token_digest=digest,
                    expires_at=now + timedelta(minutes=10),
                )
            )

            # Flush first, but do not commit until email succeeds.
            session.flush()

            try:
                send_staff_setup_email(
                    request,
                    member=member,
                    raw_token=raw_token,
                )
            except Exception:
                session.rollback()
                raise

            session.commit()

        return RedirectResponse(
            f"/staff/team/{person_id}?login_setup=sent",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    # Stuphie Add Staff Member V1
    @router.get(
        "/new",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def team_new(request: Request):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        context = common_context(request)

        context.update(
            {
                "csrf_token": team_csrf(request),
                "form_error": request.query_params.get(
                    "error",
                    "",
                ),
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="team_new.html",
            context=context,
        )


    @router.post(
        "/new",
        include_in_schema=False,
    )
    async def team_new_save(
        request: Request,
        csrf_token: str = Form(...),
        staff_name: str = Form(...),
        preferred_name: str = Form(""),
        email: str = Form(...),
        telephone: str = Form(""),
        default_role: str = Form("Photography Staff"),
        staff_code: str = Form(""),
        send_profile_invitation: str = Form("0"),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        if not valid_team_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                "/staff/team",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        clean_name = str(staff_name or "").strip()
        clean_preferred = str(
            preferred_name or ""
        ).strip()
        clean_email = str(email or "").strip().lower()
        clean_phone = str(telephone or "").strip()
        clean_role = (
            str(default_role or "").strip()
            or "Photography Staff"
        )
        clean_code = str(staff_code or "").strip()

        if not clean_name or not clean_email:
            return RedirectResponse(
                "/staff/team/new?error=missing",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        if (
            "@" not in clean_email
            or "." not in clean_email.rsplit("@", 1)[-1]
        ):
            return RedirectResponse(
                "/staff/team/new?error=email",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team/new?error=database",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        from uuid import uuid4
        from sqlalchemy import select

        source_ref = (
            "stuphie-manual-"
            + uuid4().hex
        )

        with session_factory() as session:
            existing_email = session.scalar(
                select(StaffMember).where(
                    StaffMember.email == clean_email
                )
            )

            if existing_email is not None:
                return RedirectResponse(
                    "/staff/team/new?error=duplicate_email",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            if clean_code:
                existing_code = session.scalar(
                    select(StaffMember).where(
                        StaffMember.staff_code == clean_code
                    )
                )

                if existing_code is not None:
                    return RedirectResponse(
                        "/staff/team/new?error=duplicate_code",
                        status_code=status.HTTP_303_SEE_OTHER,
                    )

            member = StaffMember(
                staff_source_ref=source_ref,
                staff_code=clean_code,
                staff_name=clean_name[:200],
                preferred_name=clean_preferred[:200],
                email=clean_email[:320],
                telephone=clean_phone[:80],
                default_role=clean_role[:160],
                active=True,
            )

            session.add(member)
            session.flush()

            member_id = str(member.id)

            if send_profile_invitation == "1":
                try:
                    create_staff_profile_request(
                        session,
                        request.app.state.settings,
                        source_ref=member.staff_source_ref,
                        staff_code=member.staff_code or "",
                        staff_name=member.staff_name or "",
                        preferred_name=member.preferred_name or "",
                        role=(
                            member.default_role
                            or "Photography Staff"
                        ),
                        staff_email=member.email,
                    )
                except Exception:
                    session.rollback()

                    return RedirectResponse(
                        "/staff/team/new?error=invitation",
                        status_code=status.HTTP_303_SEE_OTHER,
                    )

            session.commit()

        suffix = (
            "?created=1&profile_request=sent"
            if send_profile_invitation == "1"
            else "?created=1"
        )

        return RedirectResponse(
            f"/staff/team/{member_id}{suffix}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


    @router.post(
        "/{person_id}/edit",
        include_in_schema=False,
    )
    async def team_edit(
        request: Request,
        person_id: str,
        csrf_token: str = Form(...),
        staff_code: str = Form(""),
        staff_name: str = Form(...),
        preferred_name: str = Form(""),
        email: str = Form(""),
        default_role: str = Form(""),
        active: str = Form("0"),
    ):
        redirect = require_team_admin(request)

        if redirect:
            return redirect

        if not valid_team_csrf(request, csrf_token):
            return RedirectResponse(
                f"/staff/team/{person_id}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/staff/team",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        clean_staff_name = staff_name.strip()

        if not clean_staff_name:
            return RedirectResponse(
                f"/staff/team/{person_id}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with session_factory() as session:
            member = session.get(StaffMember, person_id)

            if member is None:
                return RedirectResponse(
                    "/staff/team",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            member.staff_code = staff_code.strip()
            member.staff_name = clean_staff_name
            member.preferred_name = preferred_name.strip()
            member.email = email.strip()
            member.default_role = (
                default_role.strip()
                or "Photography Staff"
            )
            member.active = active == "1"
            member.updated_at = datetime.now(timezone.utc)

            session.commit()

        return RedirectResponse(
            f"/staff/team/{person_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return router
