from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.db.models import (
    Event,
    EventAvailabilityRequest,
    EventStaffAssignment,
    EventStaffShift,
    StaffMember,
    StaffPayrollSubmission,
)

from app.auth.service import new_csrf_token
from app.db.session import database_is_ready, database_schema_is_ready
from app.migrations.runner import run_migrations

LOGGER = logging.getLogger("pirouette.staff")


def build_staff_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/staff", tags=["staff"])

    def require_staff(request: Request):
        if not request.session.get("staff_authenticated"):
            return RedirectResponse(
                "/staff/login?next=/staff",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return None

    def require_super_admin(request: Request):
        redirect = require_staff(request)
        if redirect:
            return redirect

        if (
            str(request.session.get("staff_role", ""))
            .strip()
            .lower()
            != "super_admin"
        ):
            if request.method in {"GET", "HEAD"}:
                return RedirectResponse(
                    "/staff",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            return HTMLResponse(
                "Forbidden",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        return None

    def common_context(request: Request) -> dict[str, object]:
        return {
            "staff_email": request.session.get("staff_email", ""),
            "version": request.app.state.settings.version,
        }

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request):
        redirect = require_staff(request)
        if redirect:
            return redirect

        staff_email = str(
            request.session.get("staff_email", "")
        ).strip().lower()

        is_super_admin = (
            str(request.session.get("staff_role", ""))
            .strip()
            .lower()
            == "super_admin"
        )

        member = None
        upcoming = []
        availability = []
        completed_count = 0
        pending_timesheets = 0
        management = {
            "staff_count": 0,
            "availability_waiting": 0,
            "timesheets_waiting": 0,
        }

        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is not None:
            try:
                with session_factory() as session:
                    member = session.scalar(
                        select(StaffMember).where(
                            StaffMember.email.ilike(staff_email)
                        )
                    )

                    if member is not None:
                        shift_rows = session.execute(
                            select(
                                EventStaffShift,
                                Event,
                                EventStaffAssignment,
                            )
                            .join(
                                Event,
                                Event.id == EventStaffShift.event_id,
                            )
                            .join(
                                EventStaffAssignment,
                                EventStaffAssignment.id
                                == EventStaffShift.assignment_id,
                            )
                            .where(
                                EventStaffShift.staff_member_id
                                == member.id
                            )
                            .order_by(
                                EventStaffShift.shift_date.asc(),
                                EventStaffShift.scheduled_start.asc(),
                            )
                        ).all()

                        upcoming = [
                            {
                                "shift": shift,
                                "event": event,
                                "assignment": assignment,
                            }
                            for shift, event, assignment in shift_rows
                            if shift.status in {"scheduled", "working"}
                        ]

                        completed_count = sum(
                            1
                            for shift, _event, _assignment in shift_rows
                            if shift.status == "complete"
                        )

                        pending_timesheets = sum(
                            1
                            for shift, _event, _assignment in shift_rows
                            if (
                                shift.status == "complete"
                                and not shift.approved
                            )
                        )

                        availability = list(
                            session.scalars(
                                select(EventAvailabilityRequest)
                                .where(
                                    EventAvailabilityRequest.staff_source_ref
                                    == member.staff_source_ref,
                                    EventAvailabilityRequest.status == "sent",
                                )
                                .order_by(
                                    EventAvailabilityRequest.start_date.asc()
                                )
                            )
                        )

                    if is_super_admin:
                        management["staff_count"] = int(
                            session.scalar(
                                select(func.count(StaffMember.id)).where(
                                    StaffMember.active.is_(True)
                                )
                            )
                            or 0
                        )

                        management["availability_waiting"] = int(
                            session.scalar(
                                select(
                                    func.count(EventAvailabilityRequest.id)
                                ).where(
                                    EventAvailabilityRequest.status == "sent"
                                )
                            )
                            or 0
                        )

                        management["timesheets_waiting"] = int(
                            session.scalar(
                                select(
                                    func.count(StaffPayrollSubmission.id)
                                ).where(
                                    StaffPayrollSubmission.status
                                    == "submitted"
                                )
                            )
                            or 0
                        )

            except Exception:
                LOGGER.exception("Staff portal home summary failed")

        context = common_context(request)
        context.update(
            {
                "active_nav": "dashboard",
                "member": member,
                "upcoming": upcoming,
                "next_event": upcoming[0] if upcoming else None,
                "availability_requests": availability,
                "availability_count": len(availability),
                "completed_count": completed_count,
                "pending_timesheets": pending_timesheets,
                "is_super_admin": is_super_admin,
                "management": management,
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="staff_dashboard.html",
            context=context,
        )

    @router.get("/activity", response_class=HTMLResponse, include_in_schema=False)
    async def activity(request: Request):
        redirect = require_super_admin(request)
        if redirect:
            return redirect
        rows = []
        if request.app.state.session_factory is not None:
            from sqlalchemy import select
            from app.db.models import OrderAudit
            with request.app.state.session_factory() as session:
                rows = list(session.scalars(select(OrderAudit).order_by(OrderAudit.created_at.desc()).limit(250)))
        context = common_context(request)
        context.update({"activity_rows": rows, "active_nav": "activity"})
        return templates.TemplateResponse(request=request, name="operations_activity.html", context=context)

    @router.get("/database", response_class=HTMLResponse, include_in_schema=False)
    async def database_status(request: Request, message: str = "", error: str = ""):
        redirect = require_super_admin(request)
        if redirect:
            return redirect
        engine = getattr(request.app.state, "engine", None)
        configured = bool(request.app.state.settings.database_url)
        ready = bool(engine and database_is_ready(engine))
        schema_ready = bool(engine and ready and database_schema_is_ready(engine))
        token = new_csrf_token()
        request.session["database_csrf_token"] = token
        context = common_context(request)
        context.update(
            {
                "configured": configured,
                "ready": ready,
                "schema_ready": schema_ready,
                "database_error": getattr(request.app.state, "database_error", None),
                "csrf_token": token,
                "message": message,
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request=request,
            name="database_status.html",
            context=context,
        )

    @router.post("/database/initialise", include_in_schema=False)
    async def database_initialise(request: Request, csrf_token: str = Form(...)):
        redirect = require_super_admin(request)
        if redirect:
            return redirect
        expected = str(request.session.get("database_csrf_token", ""))
        if not expected or csrf_token != expected:
            return RedirectResponse(
                "/staff/database?error=The+form+expired.+Please+try+again.",
                status_code=303,
            )
        engine = getattr(request.app.state, "engine", None)
        if engine is None or not database_is_ready(engine):
            return RedirectResponse(
                "/staff/database?error=The+database+is+not+available.",
                status_code=303,
            )
        try:
            run_migrations(engine)
        except Exception:
            LOGGER.exception("Manual database initialisation failed")
            return RedirectResponse(
                "/staff/database?error=Database+initialisation+failed.+Check+the+runtime+logs.",
                status_code=303,
            )
        return RedirectResponse(
            "/staff/database?message=Database+initialised+successfully.",
            status_code=303,
        )

    return router
