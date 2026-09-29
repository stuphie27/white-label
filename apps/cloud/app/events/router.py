from __future__ import annotations

import hmac
import calendar as month_calendar
from datetime import date, datetime, time, timezone, timedelta
from urllib.parse import quote

from sqlalchemy import select

from app.db.models import EventAvailabilityRequest, StaffEventInterest, StaffMember
from app.availability_requests import create_remote_request
from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.service import new_csrf_token
from app.db.session import database_is_ready, database_schema_is_ready
from app.events.service import (
    EVENT_STATUSES,
    PRODUCTION_CHECKLIST,
    assign_staff_to_event,
    checklist_progress,
    clock_in_event_staff_shift,
    clock_out_event_staff_shift,
    create_event,
    duplicate_event,
    get_event,
    get_event_staff_shift,
    list_active_staff,
    list_event_staff_assignments,
    event_staff_shift_map,
    get_event_staff_assignment,
    list_event_staff_shifts,
    save_event_staff_shift,
    event_staff_availability,
    event_staff_conflicts,
    event_readiness,
    list_events,
    remove_staff_from_event,
    staffing_summary,
    update_event,
)


def _require_staff(request: Request):
    if not request.session.get("staff_authenticated"):
        return RedirectResponse("/staff/login", status_code=status.HTTP_303_SEE_OTHER)
    return None


def _csrf(request: Request) -> str:
    token = new_csrf_token()
    request.session["event_csrf_token"] = token
    return token


def _valid_csrf(request: Request, supplied: str) -> bool:
    expected = str(request.session.get("event_csrf_token", ""))
    return bool(expected and hmac.compare_digest(expected, supplied))





def _send_inline_staff_confirmation(
    session,
    request: Request,
    *,
    event,
    staff_member: StaffMember,
) -> bool:
    event_ref = str(event.source_ref or "").strip()
    staff_ref = str(
        staff_member.staff_source_ref or ""
    ).strip()
    staff_email = str(
        staff_member.email or ""
    ).strip()

    if not (
        event_ref
        and staff_ref
        and staff_email
    ):
        return False

    today = date.today()
    preferred_lock = (
        event.start_date
        - timedelta(days=30)
    )
    lock_date = max(
        preferred_lock,
        today + timedelta(days=1),
    )

    source_ref = (
        "STUPHIE-AVAIL-"
        f"{event.id}-"
        f"{staff_member.id}"
    )

    try:
        result = create_remote_request(
            session,
            request.app.state.settings,
            source_ref=source_ref,
            event_source_ref=event_ref,
            event_name=event.name,
            venue=event.venue or "",
            start_date=event.start_date,
            end_date=event.end_date,
            lock_date=lock_date,
            staff_source_ref=staff_ref,
            staff_name=(
                staff_member.preferred_name
                or staff_member.staff_name
            ),
            staff_email=staff_email,
            force_send=True,
        )
        return bool(result.get("emailed"))
    except Exception:
        return False


def _availability_status_map(session, event) -> dict[str, str]:
    event_ref = str(event.source_ref or "").strip()
    if not event_ref:
        return {}
    rows = list(session.scalars(select(EventAvailabilityRequest).where(
        EventAvailabilityRequest.event_source_ref == event_ref
    )))
    latest_by_staff = {}
    for row in rows:
        staff_ref = str(row.staff_source_ref or "").strip()
        if not staff_ref:
            continue
        current = latest_by_staff.get(staff_ref)
        if current is None or row.updated_at > current.updated_at:
            latest_by_staff[staff_ref] = row

    result = {}
    for staff_ref, row in latest_by_staff.items():
        availability = str(row.availability_status or "").strip().lower()
        request_status = str(row.status or "").strip().lower()
        if request_status == "submitted" and availability == "available":
            state = "available"
        elif request_status == "submitted" and availability == "unavailable":
            state = "unavailable"
        elif request_status == "email_failed":
            state = "email_failed"
        else:
            state = "waiting"
        result[staff_ref] = state
    return result


def _close_surplus_event_interest(
    session,
    *,
    event,
    selected_staff_id: str,
) -> int:
    required = max(int(event.staff_required or 0), 0)

    if required <= 0:
        return 0

    assignments = list_event_staff_assignments(
        session,
        event.id,
    )

    assigned_count = sum(
        1
        for assignment, _staff in assignments
        if assignment.assignment_status != "cancelled"
    )

    if assigned_count < required:
        return 0

    rows = list(
        session.scalars(
            select(StaffEventInterest).where(
                StaffEventInterest.event_id == event.id,
                StaffEventInterest.status == "interested",
                StaffEventInterest.staff_member_id
                != selected_staff_id,
            )
        )
    )

    changed = 0

    for interest in rows:
        interest.status = "not_selected"
        changed += 1

    if changed:
        session.commit()

    return changed


def _database_unavailable(request: Request, templates: Jinja2Templates):
    engine = getattr(request.app.state, "engine", None)
    session_factory = getattr(request.app.state, "session_factory", None)
    connection_ready = bool(engine and database_is_ready(engine))
    schema_ready = bool(engine and connection_ready and database_schema_is_ready(engine))
    if session_factory is not None and schema_ready:
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


def _parse_form(name: str, start_date: str, end_date: str, status_value: str) -> tuple[date, date, str]:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Event name is required.")
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("End date cannot be before the start date.")
    if status_value not in EVENT_STATUSES:
        raise ValueError("Choose a valid event status.")
    return start, end, clean_name




def _parse_optional_time(value: str, label: str) -> time | None:
    clean = value.strip()
    if not clean:
        return None
    try:
        return time.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid time.") from exc


def _operational_values(
    *,
    client_name: str,
    principal_name: str,
    photographer_name: str,
    dance_style: str,
    arrival_time: str,
    photography_start: str,
    photography_finish: str,
) -> dict[str, object]:
    arrival = _parse_optional_time(arrival_time, "Arrival time")
    photo_start = _parse_optional_time(photography_start, "Photography start")
    photo_finish = _parse_optional_time(photography_finish, "Photography finish")
    if photo_start and photo_finish and photo_finish < photo_start:
        raise ValueError("Photography finish cannot be before photography start.")
    return {
        "client_name": client_name.strip(),
        "principal_name": principal_name.strip(),
        "photographer_name": photographer_name.strip(),
        "dance_style": dance_style.strip(),
        "arrival_time": arrival,
        "photography_start": photo_start,
        "photography_finish": photo_finish,
    }


def _parse_optional_int(value: str, label: str) -> int | None:
    clean = value.strip()

    if not clean:
        return None

    try:
        parsed = int(clean)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number.") from exc

    if parsed < 0:
        raise ValueError(f"{label} cannot be negative.")

    return parsed


def _planning_values(
    *,
    promoter_name: str,
    promoter_email: str,
    promoter_phone: str,
    staff_required: str,
    event_start_time: str,
    event_end_time: str,
    travel_time_minutes: str,
    hotel_required: str,
    hotel_info: str,
    planning_notes: str,
    photography_enabled: str,
    video_enabled: str,
    printing_enabled: str,
    customer_kiosk_enabled: str,
    event_ready: str,
) -> dict[str, object]:
    start_time = _parse_optional_time(
        event_start_time,
        "Event start time",
    )

    finish_time = _parse_optional_time(
        event_end_time,
        "Event finish time",
    )

    if start_time and finish_time and finish_time < start_time:
        raise ValueError(
            "Event finish time cannot be before event start time."
        )

    return {
        "promoter_name": promoter_name.strip(),
        "promoter_email": promoter_email.strip(),
        "promoter_phone": promoter_phone.strip(),
        "staff_required": _parse_optional_int(
            staff_required,
            "Staff required",
        ),
        "event_start_time": start_time,
        "event_end_time": finish_time,
        "travel_time_minutes": _parse_optional_int(
            travel_time_minutes,
            "Travel time",
        ),
        "hotel_required": hotel_required == "1",
        "hotel_info": hotel_info.strip(),
        "planning_notes": planning_notes.strip(),
        "photography_enabled": photography_enabled == "1",
        "video_enabled": video_enabled == "1",
        "printing_enabled": printing_enabled == "1",
        "customer_kiosk_enabled": customer_kiosk_enabled == "1",

        # Event Ready is a controlled action from Event Control.
        # Editing planning information always requires management review again.
        "event_ready": False,
    }

def build_events_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/staff/events", tags=["staff-events"])

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    async def events_index(request: Request, q: str = "", status_filter: str = ""):

        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        clean_status = (
            status_filter
            if status_filter != "archived"
            else ""
        )

        with request.app.state.session_factory() as session:
            events = list_events(
                session,
                query=q,
                status=clean_status,
                include_archived=False,
            )
            active_staff = list_active_staff(session)
            event_rows = []
            for event in events:
                assignments = list_event_staff_assignments(session, event.id)
                availability = event_staff_availability(
                    session, event=event, staff_members=active_staff
                )
                conflicts = event_staff_conflicts(
                    session, event=event, staff_members=active_staff
                )
                assigned_ids = {
                    staff.id
                    for _assignment, staff in assignments
                }

                interest_rows = session.execute(
                    select(
                        StaffEventInterest,
                        StaffMember,
                    )
                    .join(
                        StaffMember,
                        StaffMember.id
                        == StaffEventInterest.staff_member_id,
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

                interested_unassigned = [
                    (interest, staff)
                    for interest, staff in interest_rows
                    if staff.id not in assigned_ids
                ]

                assignable_staff = [
                    staff
                    for staff in active_staff
                    if staff.id not in assigned_ids
                ]

                event_rows.append({
                    "event": event,
                    "staffing": staffing_summary(event, assignments),
                    "readiness": event_readiness(
                        event, assignments, availability, conflicts
                    ),
                    "assignments": assignments,
                    "interests": interested_unassigned,
                    "assignable_staff": assignable_staff,
                    "availability_statuses": _availability_status_map(session, event),
                })

        active_statuses = tuple(
            value
            for value in EVENT_STATUSES
            if value != "archived"
        )

        return templates.TemplateResponse(
            request=request,
            name="events_list.html",
            context={
                "events": events,
                "event_rows": event_rows,
                "statuses": active_statuses,
                "query": q,
                "status_filter": clean_status,
                "staff_email": request.session.get("staff_email", ""),
                "csrf_token": _csrf(request),
            },
        )

    @router.get(
        "/completed",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def completed_events(
        request: Request,
        q: str = "",
    ):
        redirect = _require_staff(request)

        if redirect:
            return redirect

        unavailable = _database_unavailable(
            request,
            templates,
        )

        if unavailable:
            return unavailable

        with request.app.state.session_factory() as session:
            events = list_events(
                session,
                query=q,
                status="archived",
            )

            events.reverse()

        return templates.TemplateResponse(
            request=request,
            name="completed_events.html",
            context={
                "events": events,
                "query": q,
                "staff_email": request.session.get(
                    "staff_email",
                    "",
                ),
            },
        )


    @router.get("/calendar", response_class=HTMLResponse, include_in_schema=False)
    async def events_calendar(request: Request, year: int | None = None, month: int | None = None):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable

        today = date.today()
        view_year = year or today.year
        view_month = month or today.month
        if view_month < 1 or view_month > 12:
            view_year, view_month = today.year, today.month

        first = date(view_year, view_month, 1)
        if view_month == 12:
            next_first = date(view_year + 1, 1, 1)
        else:
            next_first = date(view_year, view_month + 1, 1)
        if view_month == 1:
            prev_year, prev_month = view_year - 1, 12
        else:
            prev_year, prev_month = view_year, view_month - 1
        if view_month == 12:
            next_year, next_month = view_year + 1, 1
        else:
            next_year, next_month = view_year, view_month + 1

        grid = month_calendar.Calendar(firstweekday=0).monthdatescalendar(view_year, view_month)
        range_start = grid[0][0]
        range_end = grid[-1][-1]

        with request.app.state.session_factory() as session:
            all_events = list_events(session, include_archived=False)
            active_staff = list_active_staff(session)
            rows = []
            for event in all_events:
                if event.start_date > range_end or event.end_date < range_start:
                    continue
                assignments = list_event_staff_assignments(session, event.id)
                availability = event_staff_availability(
                    session, event=event, staff_members=active_staff
                )
                conflicts = event_staff_conflicts(
                    session, event=event, staff_members=active_staff
                )
                rows.append({
                    "event": event,
                    "readiness": event_readiness(
                        event, assignments, availability, conflicts
                    ),
                })

        weeks = []
        for week in grid:
            cells = []
            for day in week:
                day_events = [
                    row for row in rows
                    if row["event"].start_date <= day <= row["event"].end_date
                ]
                cells.append({
                    "date": day,
                    "in_month": day.month == view_month,
                    "today": day == today,
                    "events": day_events,
                })
            weeks.append(cells)

        return templates.TemplateResponse(
            request=request,
            name="events_calendar.html",
            context={
                "calendar_title": first.strftime("%B %Y"),
                "calendar_weeks": weeks,
                "prev_year": prev_year,
                "prev_month": prev_month,
                "next_year": next_year,
                "next_month": next_month,
                "staff_email": request.session.get("staff_email", ""),
            },
        )

    @router.get("/new", response_class=HTMLResponse, include_in_schema=False)
    async def event_new(request: Request, error: str = ""):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        return templates.TemplateResponse(
            request=request,
            name="event_form.html",
            context={
                "event": None,
                "statuses": EVENT_STATUSES,
                "csrf_token": _csrf(request),
                "error": error,
                "form_action": "/staff/events/new",
                "page_title": "Create event",
            },
        )

    @router.post("/new", include_in_schema=False)
    async def event_create(
        request: Request,
        name: str = Form(...),
        start_date: str = Form(...),
        end_date: str = Form(...),
        client_name: str = Form(""),
        principal_name: str = Form(""),
        photographer_name: str = Form(""),
        dance_style: str = Form(""),
        arrival_time: str = Form(""),
        photography_start: str = Form(""),
        photography_finish: str = Form(""),
        venue: str = Form(""),
        description: str = Form(""),
        internal_notes: str = Form(""),

        promoter_name: str = Form(""),
        promoter_email: str = Form(""),
        promoter_phone: str = Form(""),
        staff_required: str = Form(""),
        event_start_time: str = Form(""),
        event_end_time: str = Form(""),
        travel_time_minutes: str = Form(""),
        hotel_required: str = Form(""),
        hotel_info: str = Form(""),
        planning_notes: str = Form(""),

        photography_enabled: str = Form(""),
        video_enabled: str = Form(""),
        printing_enabled: str = Form(""),
        customer_kiosk_enabled: str = Form(""),
        event_ready: str = Form(""),

        event_status: str = Form("draft"),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse("/staff/events/new?error=" + quote("The form expired. Please try again."), status_code=303)
        try:
            start, end, clean_name = _parse_form(name, start_date, end_date, event_status)
            operations = _operational_values(
                client_name=client_name, principal_name=principal_name,
                photographer_name=photographer_name, dance_style=dance_style,
                arrival_time=arrival_time, photography_start=photography_start,
                photography_finish=photography_finish,
            )

            planning = _planning_values(
                promoter_name=promoter_name,
                promoter_email=promoter_email,
                promoter_phone=promoter_phone,
                staff_required=staff_required,
                event_start_time=event_start_time,
                event_end_time=event_end_time,
                travel_time_minutes=travel_time_minutes,
                hotel_required=hotel_required,
                hotel_info=hotel_info,
                planning_notes=planning_notes,
                photography_enabled=photography_enabled,
                video_enabled=video_enabled,
                printing_enabled=printing_enabled,
                customer_kiosk_enabled=customer_kiosk_enabled,
                event_ready=event_ready,
            )
        except ValueError as exc:
            return RedirectResponse("/staff/events/new?error=" + quote(str(exc)), status_code=303)
        with request.app.state.session_factory() as session:
            event = create_event(
                session,
                name=clean_name,
                **operations,
                **planning,
                start_date=start,
                end_date=end,
                venue=venue.strip(),
                description=description.strip(),
                internal_notes=internal_notes.strip(),
                status=event_status,
            )
        return RedirectResponse(f"/staff/events/{event.id}", status_code=303)

    @router.get("/{event_id}", response_class=HTMLResponse, include_in_schema=False)
    async def event_detail(request: Request, event_id: str):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)

            staff_interest_rows = []

            if event is not None:

                staff_interest_rows = session.execute(
                    select(
                        StaffEventInterest,
                        StaffMember,
                    )
                    .join(
                        StaffMember,
                        StaffMember.id
                        == StaffEventInterest.staff_member_id,
                    )
                    .where(
                        StaffEventInterest.event_id
                        == event.id,
                        StaffEventInterest.status
                        == "interested",
                    )
                    .order_by(
                        StaffMember.preferred_name.asc(),
                        StaffMember.staff_name.asc(),
                    )
                ).all()

                staff_assignments = list_event_staff_assignments(session, event_id)
                available_staff = list_active_staff(session)
                staff_summary = staffing_summary(event, staff_assignments)
                staff_shifts = list_event_staff_shifts(session, event_id)
                staff_shift_map = event_staff_shift_map(staff_shifts)

                staff_hours = {}

                for assignment, staff in staff_assignments:
                    total_minutes = 0

                    for shift in staff_shift_map.get(
                        assignment.id,
                        [],
                    ):
                        if (
                            shift.actual_clock_in is not None
                            and shift.actual_clock_out is not None
                        ):
                            seconds = (
                                shift.actual_clock_out
                                - shift.actual_clock_in
                            ).total_seconds()

                            total_minutes += max(
                                0,
                                int(seconds // 60)
                                - int(shift.break_minutes or 0),
                            )

                    staff_hours[staff.id] = total_minutes

                staff_availability = event_staff_availability(
                    session,
                    event=event,
                    staff_members=available_staff,
                )

                staff_conflicts = event_staff_conflicts(
                    session,
                    event=event,
                    staff_members=available_staff,
                )

                readiness = event_readiness(
                    event,
                    staff_assignments,
                    staff_availability,
                    staff_conflicts,
                )
            else:
                staff_assignments = []
                available_staff = []
                staff_summary = {
                    "required": 0,
                    "assigned": 0,
                    "outstanding": 0,
                }
                staff_availability = {}
                staff_conflicts = {}
                readiness = {
                    "missing": [],
                    "complete": False,
                    "confirmed": False,
                    "ready": False,
                    "needs_attention": True,
                    "required": 0,
                    "assigned": 0,
                    "effective_assigned": 0,
                    "unavailable_assigned": 0,
                    "shortfall": 0,
                    "conflict_count": 0,
                }
                staff_shifts = []
                staff_shift_map = {}
                staff_hours = {}

        if event is None:
            return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)

        completed, checklist_total, checklist_percentage = checklist_progress(event)
        return templates.TemplateResponse(
            request=request,
            name="event_detail.html",
            context={
                "event": event,
                "csrf_token": _csrf(request),
                "production_checklist": PRODUCTION_CHECKLIST,
                "checklist_completed": completed,
                "checklist_total": checklist_total,
                "checklist_percentage": checklist_percentage,
                "staff_assignments": staff_assignments,
                "available_staff": available_staff,
                "staff_summary": staff_summary,
                "staff_availability": staff_availability,
                "staff_conflicts": staff_conflicts,
                "readiness": readiness,
                "staff_shifts": staff_shifts,
                "staff_shift_map": staff_shift_map,
                "staff_hours": staff_hours,
                "staff_interest_rows": staff_interest_rows,
            },
        )

    @router.post("/{event_id}/staff", include_in_schema=False)
    async def event_assign_staff(
        request: Request,
        event_id: str,
        staff_member_id: str = Form(...),
        role: str = Form(""),
        notes: str = Form(""),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect

        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable

        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            from app.db.models import StaffMember

            staff_member = session.get(StaffMember, staff_member_id)

            if staff_member is not None and staff_member.active:
                assignment = assign_staff_to_event(
                    session,
                    event=event,
                    staff_member=staff_member,
                    role=role,
                    notes=notes,
                )

                # Assigning staff automatically asks them to confirm
                # availability. Keep the assignment even if email
                # delivery itself fails.
                event_ref = str(event.source_ref or "").strip()
                staff_ref = str(
                    staff_member.staff_source_ref or ""
                ).strip()
                staff_email = str(
                    staff_member.email or ""
                ).strip()

                if (
                    event_ref
                    and staff_ref
                    and staff_email
                ):
                    today = date.today()

                    preferred_lock = (
                        event.start_date
                        - timedelta(days=30)
                    )

                    # For events already inside the normal 30-day
                    # deadline, still give staff a live response link.
                    lock_date = max(
                        preferred_lock,
                        today + timedelta(days=1),
                    )

                    source_ref = (
                        "STUPHIE-AVAIL-"
                        f"{event.id}-"
                        f"{staff_member.id}"
                    )

                    try:
                        create_remote_request(
                            session,
                            request.app.state.settings,
                            source_ref=source_ref,
                            event_source_ref=event_ref,
                            event_name=event.name,
                            venue=event.venue or "",
                            start_date=event.start_date,
                            end_date=event.end_date,
                            lock_date=lock_date,
                            staff_source_ref=staff_ref,
                            staff_name=(
                                staff_member.preferred_name
                                or staff_member.staff_name
                            ),
                            staff_email=staff_email,
                        )
                    except Exception:
                        # Assignment must not be lost simply because
                        # SMTP is temporarily unavailable.
                        pass

                # Staffing changed: management must review Event Ready again.
                update_event(
                    session,
                    event,
                    event_ready=False,
                    event_ready_at=None,
                )

        return RedirectResponse(
            f"/staff/events/{event_id}",
            status_code=303,
        )


    @router.post(
        "/{event_id}/staff/{staff_member_id}/shift/save",
        include_in_schema=False,
    )
    async def event_save_staff_shift(
        request: Request,
        event_id: str,
        staff_member_id: str,
        shift_date: str = Form(...),
        scheduled_start: str = Form(""),
        scheduled_finish: str = Form(""),
        break_minutes: int = Form(0),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)

        if redirect:
            return redirect

        unavailable = _database_unavailable(
            request,
            templates,
        )

        if unavailable:
            return unavailable

        if not _valid_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        try:
            parsed_date = date.fromisoformat(
                shift_date.strip()
            )

            parsed_start = (
                time.fromisoformat(
                    scheduled_start.strip()
                )
                if scheduled_start.strip()
                else None
            )

            parsed_finish = (
                time.fromisoformat(
                    scheduled_finish.strip()
                )
                if scheduled_finish.strip()
                else None
            )

        except ValueError:
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(
                session,
                event_id,
            )

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            assignment = get_event_staff_assignment(
                session,
                event_id=event_id,
                staff_member_id=staff_member_id,
            )

            if assignment is not None:
                try:
                    save_event_staff_shift(
                        session,
                        event=event,
                        assignment=assignment,
                        shift_date=parsed_date,
                        scheduled_start=parsed_start,
                        scheduled_finish=parsed_finish,
                        break_minutes=break_minutes,
                    )
                except ValueError:
                    return RedirectResponse(
                        f"/staff/events/{event_id}",
                        status_code=303,
                    )

        return RedirectResponse(
            f"/staff/events/{event_id}",
            status_code=303,
        )


    @router.post(
        "/{event_id}/staff/{staff_member_id}/shift/{shift_id}/clock-in",
        include_in_schema=False,
    )
    async def event_staff_shift_clock_in(
        request: Request,
        event_id: str,
        staff_member_id: str,
        shift_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)

        if redirect:
            return redirect

        unavailable = _database_unavailable(
            request,
            templates,
        )

        if unavailable:
            return unavailable

        if not _valid_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(
                session,
                event_id,
            )

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            shift = get_event_staff_shift(
                session,
                event_id=event_id,
                staff_member_id=staff_member_id,
                shift_id=shift_id,
            )

            if shift is not None:
                try:
                    clock_in_event_staff_shift(
                        session,
                        shift=shift,
                    )
                except ValueError:
                    pass

        return RedirectResponse(
            f"/staff/events/{event_id}",
            status_code=303,
        )


    @router.post(
        "/{event_id}/staff/{staff_member_id}/shift/{shift_id}/clock-out",
        include_in_schema=False,
    )
    async def event_staff_shift_clock_out(
        request: Request,
        event_id: str,
        staff_member_id: str,
        shift_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)

        if redirect:
            return redirect

        unavailable = _database_unavailable(
            request,
            templates,
        )

        if unavailable:
            return unavailable

        if not _valid_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(
                session,
                event_id,
            )

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            shift = get_event_staff_shift(
                session,
                event_id=event_id,
                staff_member_id=staff_member_id,
                shift_id=shift_id,
            )

            if shift is not None:
                try:
                    clock_out_event_staff_shift(
                        session,
                        shift=shift,
                    )
                except ValueError:
                    pass

        return RedirectResponse(
            f"/staff/events/{event_id}",
            status_code=303,
        )


    @router.post(
        "/{event_id}/staff/{staff_member_id}/remove",
        include_in_schema=False,
    )
    async def event_remove_staff(
        request: Request,
        event_id: str,
        staff_member_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect

        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable

        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            remove_staff_from_event(
                session,
                event_id=event_id,
                staff_member_id=staff_member_id,
            )

            # Staffing changed: management must review Event Ready again.
            update_event(
                session,
                event,
                event_ready=False,
                event_ready_at=None,
            )

        return RedirectResponse(
            f"/staff/events/{event_id}",
            status_code=303,
        )

    @router.post("/{event_id}/planning-inline", include_in_schema=False)
    async def event_inline_planning_update(
        request: Request,
        event_id: str,
        name: str = Form(...),
        venue: str = Form(""),
        dance_style: str = Form(""),
        start_date: str = Form(...),
        end_date: str = Form(...),
        client_name: str = Form(""),
        photographer_name: str = Form(""),
        staff_required: str = Form(""),
        event_start_time: str = Form(""),
        event_end_time: str = Form(""),
        travel_time_minutes: str = Form(""),
        hotel_required: str = Form("0"),
        hotel_info: str = Form(""),
        promoter_name: str = Form(""),
        promoter_email: str = Form(""),
        promoter_phone: str = Form(""),
        planning_notes: str = Form(""),
        photography_enabled: str = Form(""),
        video_enabled: str = Form(""),
        printing_enabled: str = Form(""),
        customer_kiosk_enabled: str = Form(""),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect

        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable

        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(
                f"/staff/events?open={event_id}&inline_error=expired#event-{event_id}",
                status_code=303,
            )

        clean_name = name.strip()
        if not clean_name:
            return RedirectResponse(
                f"/staff/events?open={event_id}&inline_error=name#event-{event_id}",
                status_code=303,
            )

        try:
            parsed_start_date = date.fromisoformat(start_date)
            parsed_end_date = date.fromisoformat(end_date)

            if parsed_end_date < parsed_start_date:
                raise ValueError(
                    "Event end date cannot be before event start date."
                )

            parsed_start = _parse_optional_time(
                event_start_time,
                "Event start time",
            )
            parsed_finish = _parse_optional_time(
                event_end_time,
                "Event finish time",
            )

            if (
                parsed_start_date == parsed_end_date
                and parsed_start
                and parsed_finish
                and parsed_finish < parsed_start
            ):
                raise ValueError(
                    "Event finish time cannot be before event start time."
                )

            parsed_staff_required = _parse_optional_int(
                staff_required,
                "Staff required",
            )
            parsed_travel = _parse_optional_int(
                travel_time_minutes,
                "Travel time",
            )
        except ValueError:
            return RedirectResponse(
                f"/staff/events?open={event_id}&inline_error=values#event-{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            if event.status == "archived":
                return RedirectResponse(
                    "/staff/events/completed",
                    status_code=303,
                )

            update_event(
                session,
                event,
                name=clean_name,
                venue=venue.strip(),
                dance_style=dance_style.strip(),
                start_date=parsed_start_date,
                end_date=parsed_end_date,
                client_name=client_name.strip(),
                photographer_name=photographer_name.strip(),
                staff_required=parsed_staff_required,
                event_start_time=parsed_start,
                event_end_time=parsed_finish,
                travel_time_minutes=parsed_travel,
                hotel_required=hotel_required == "1",
                hotel_info=hotel_info.strip(),
                promoter_name=promoter_name.strip(),
                promoter_email=promoter_email.strip(),
                promoter_phone=promoter_phone.strip(),
                planning_notes=planning_notes.strip(),
                photography_enabled=photography_enabled == "1",
                video_enabled=video_enabled == "1",
                printing_enabled=printing_enabled == "1",
                customer_kiosk_enabled=customer_kiosk_enabled == "1",
                event_ready=False,
                event_ready_at=None,
            )

        return RedirectResponse(
            f"/staff/events?open={event_id}&saved={event_id}#event-{event_id}",
            status_code=303,
        )


    @router.post("/{event_id}/staff-inline", include_in_schema=False)
    async def event_assign_staff_inline(
        request: Request,
        event_id: str,
        staff_member_id: str = Form(...),
        role: str = Form(""),
        notes: str = Form(""),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect

        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable

        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(
                f"/staff/events?open={event_id}#event-{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)
            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            staff_member = session.get(StaffMember, staff_member_id)

            email_sent = False
            interests_closed = 0

            if staff_member is not None and staff_member.active:
                assign_staff_to_event(
                    session,
                    event=event,
                    staff_member=staff_member,
                    role=role,
                    notes=notes,
                )

                email_sent = _send_inline_staff_confirmation(
                    session,
                    request,
                    event=event,
                    staff_member=staff_member,
                )

                interests_closed = _close_surplus_event_interest(
                    session,
                    event=event,
                    selected_staff_id=staff_member.id,
                )

                update_event(
                    session,
                    event,
                    event_ready=False,
                    event_ready_at=None,
                )

        result = "assigned"

        if staff_member is not None and staff_member.active:
            if email_sent:
                result = "confirmation_sent"
            else:
                result = "assigned_email_pending"

            if interests_closed:
                result += f"_closed_{interests_closed}"

        return RedirectResponse(
            f"/staff/events?open={event_id}&staff_saved={result}#event-{event_id}",
            status_code=303,
        )


    @router.post(
        "/{event_id}/staff/{staff_member_id}/remove-inline",
        include_in_schema=False,
    )
    async def event_remove_staff_inline(
        request: Request,
        event_id: str,
        staff_member_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect

        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable

        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(
                f"/staff/events?open={event_id}#event-{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            remove_staff_from_event(
                session,
                event_id=event_id,
                staff_member_id=staff_member_id,
            )

            update_event(
                session,
                event,
                event_ready=False,
                event_ready_at=None,
            )

        return RedirectResponse(
            f"/staff/events?open={event_id}&staff_saved=1#event-{event_id}",
            status_code=303,
        )


    @router.post("/{event_id}/cancel-inline", include_in_schema=False)
    async def event_cancel_inline(
        request: Request,
        event_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect

        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable

        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(
                f"/staff/events?open={event_id}&inline_error=expired#event-{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            if str(event.status or "").lower() in {
                "complete",
                "archived",
                "cancelled",
            }:
                return RedirectResponse(
                    f"/staff/events?open={event_id}#event-{event_id}",
                    status_code=303,
                )

            assignments = list_event_staff_assignments(
                session,
                event_id,
            )

            for assignment, _staff in assignments:
                assignment.assignment_status = "cancelled"

            update_event(
                session,
                event,
                status="cancelled",
                event_ready=False,
                event_ready_at=None,
            )

        return RedirectResponse(
            "/staff/events?cancelled=1",
            status_code=303,
        )


    @router.get("/{event_id}/edit", response_class=HTMLResponse, include_in_schema=False)
    async def event_edit(request: Request, event_id: str, error: str = ""):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)
        if event is None:
            return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
        return templates.TemplateResponse(
            request=request,
            name="event_form.html",
            context={
                "event": event,
                "statuses": EVENT_STATUSES,
                "csrf_token": _csrf(request),
                "error": error,
                "form_action": f"/staff/events/{event_id}/edit",
                "page_title": "Edit event",
            },
        )

    @router.post("/{event_id}/edit", include_in_schema=False)
    async def event_update(
        request: Request,
        event_id: str,
        name: str = Form(...),
        start_date: str = Form(...),
        end_date: str = Form(...),
        client_name: str = Form(""),
        principal_name: str = Form(""),
        photographer_name: str = Form(""),
        dance_style: str = Form(""),
        arrival_time: str = Form(""),
        photography_start: str = Form(""),
        photography_finish: str = Form(""),
        venue: str = Form(""),
        description: str = Form(""),
        internal_notes: str = Form(""),

        promoter_name: str = Form(""),
        promoter_email: str = Form(""),
        promoter_phone: str = Form(""),
        staff_required: str = Form(""),
        event_start_time: str = Form(""),
        event_end_time: str = Form(""),
        travel_time_minutes: str = Form(""),
        hotel_required: str = Form(""),
        hotel_info: str = Form(""),
        planning_notes: str = Form(""),

        photography_enabled: str = Form(""),
        video_enabled: str = Form(""),
        printing_enabled: str = Form(""),
        customer_kiosk_enabled: str = Form(""),
        event_ready: str = Form(""),

        event_status: str = Form("draft"),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(f"/staff/events/{event_id}/edit?error=" + quote("The form expired. Please try again."), status_code=303)
        try:
            start, end, clean_name = _parse_form(name, start_date, end_date, event_status)
            operations = _operational_values(
                client_name=client_name, principal_name=principal_name,
                photographer_name=photographer_name, dance_style=dance_style,
                arrival_time=arrival_time, photography_start=photography_start,
                photography_finish=photography_finish,
            )

            planning = _planning_values(
                promoter_name=promoter_name,
                promoter_email=promoter_email,
                promoter_phone=promoter_phone,
                staff_required=staff_required,
                event_start_time=event_start_time,
                event_end_time=event_end_time,
                travel_time_minutes=travel_time_minutes,
                hotel_required=hotel_required,
                hotel_info=hotel_info,
                planning_notes=planning_notes,
                photography_enabled=photography_enabled,
                video_enabled=video_enabled,
                printing_enabled=printing_enabled,
                customer_kiosk_enabled=customer_kiosk_enabled,
                event_ready=event_ready,
            )
        except ValueError as exc:
            return RedirectResponse(f"/staff/events/{event_id}/edit?error=" + quote(str(exc)), status_code=303)
        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)
            if event is None:
                return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
            # Archived events may only leave the archive through
            # the dedicated Reopen Event action. This protects against
            # stale Edit Event forms accidentally resurrecting a
            # completed event.
            safe_event_status = (
                "archived"
                if event.status == "archived"
                else event_status
            )

            update_event(
                session,
                event,
                name=clean_name,
                **operations,
                **planning,
                start_date=start,
                end_date=end,
                venue=venue.strip(),
                description=description.strip(),
                internal_notes=internal_notes.strip(),
                status=safe_event_status,
            )
        return RedirectResponse(f"/staff/events/{event_id}", status_code=303)


    @router.post("/{event_id}/checklist", include_in_schema=False)
    async def event_checklist_update(
        request: Request,
        event_id: str,
        task: str = Form(...),
        completed: str = Form("0"),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        valid_tasks = {field for field, _label in PRODUCTION_CHECKLIST}
        if not _valid_csrf(request, csrf_token) or task not in valid_tasks:
            return RedirectResponse(f"/staff/events/{event_id}", status_code=303)
        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)
            if event is None:
                return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
            update_event(session, event, **{task: completed == "1"})
        return RedirectResponse(f"/staff/events/{event_id}#production-checklist", status_code=303)

    @router.post("/{event_id}/status", include_in_schema=False)
    async def event_status_update(
        request: Request,
        event_id: str,
        event_status: str = Form(...),
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        if not _valid_csrf(request, csrf_token) or event_status not in EVENT_STATUSES:
            return RedirectResponse(f"/staff/events/{event_id}", status_code=303)
        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)
            if event is None:
                return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)

            # Completed/archived events are locked. The dedicated
            # Reopen Event route is the only supported transition
            # out of archived status.
            if event.status == "archived":
                return RedirectResponse(
                    f"/staff/events/{event_id}#complete-event",
                    status_code=303,
                )

            update_event(session, event, status=event_status)
        return RedirectResponse(f"/staff/events/{event_id}", status_code=303)

    @router.post("/{event_id}/duplicate", include_in_schema=False)
    async def event_duplicate(request: Request, event_id: str, csrf_token: str = Form(...)):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(f"/staff/events/{event_id}", status_code=303)
        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)
            if event is None:
                return templates.TemplateResponse(request=request, name="404.html", context={}, status_code=404)
            copy = duplicate_event(session, event)
        return RedirectResponse(f"/staff/events/{copy.id}/edit", status_code=303)

    @router.post("/{event_id}/archive", include_in_schema=False)
    async def event_archive(request: Request, event_id: str, csrf_token: str = Form(...)):
        redirect = _require_staff(request)
        if redirect:
            return redirect
        unavailable = _database_unavailable(request, templates)
        if unavailable:
            return unavailable
        if not _valid_csrf(request, csrf_token):
            return RedirectResponse(f"/staff/events/{event_id}", status_code=303)
        with request.app.state.session_factory() as session:
            event = get_event(session, event_id)

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            assignments = list_event_staff_assignments(
                session,
                event_id,
            )

            staff_members = list_active_staff(
                session,
            )

            availability = event_staff_availability(
                session,
                event=event,
                staff_members=staff_members,
            )

            conflicts = event_staff_conflicts(
                session,
                event=event,
                staff_members=staff_members,
            )

            readiness = event_readiness(
                event,
                assignments,
                availability,
                conflicts,
            )

            if readiness["missing"]:
                return RedirectResponse(
                    f"/staff/events/{event_id}"
                    "?close_error=1"
                    "#complete-event",
                    status_code=303,
                )

            update_event(
                session,
                event,
                status="archived",
            )

        return RedirectResponse(
            f"/staff/events/{event_id}"
            "#complete-event",
            status_code=303,
        )


    @router.post(
        "/{event_id}/reopen",
        include_in_schema=False,
    )
    async def event_reopen(
        request: Request,
        event_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)

        if redirect:
            return redirect

        if not _valid_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(
                session,
                event_id,
            )

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            if event.status == "archived":
                update_event(
                    session,
                    event,
                    status="draft",
                )

        return RedirectResponse(
            f"/staff/events/{event_id}",
            status_code=303,
        )


    @router.post(
        "/{event_id}/ready",
        include_in_schema=False,
    )
    async def event_mark_ready(
        request: Request,
        event_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)

        if redirect:
            return redirect

        unavailable = _database_unavailable(
            request,
            templates,
        )

        if unavailable:
            return unavailable

        if not _valid_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(
                session,
                event_id,
            )

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            assignments = list_event_staff_assignments(
                session,
                event_id,
            )

            staff_members = list_active_staff(
                session,
            )

            availability = event_staff_availability(
                session,
                event=event,
                staff_members=staff_members,
            )

            conflicts = event_staff_conflicts(
                session,
                event=event,
                staff_members=staff_members,
            )

            readiness = event_readiness(
                event,
                assignments,
                availability,
                conflicts,
            )

            if readiness["missing"]:
                return RedirectResponse(
                    f"/staff/events/{event_id}?ready_error=1",
                    status_code=303,
                )

            update_event(
                session,
                event,
                event_ready=True,
                event_ready_at=datetime.now(timezone.utc),
            )

        return RedirectResponse(
            f"/staff/events/{event_id}",
            status_code=303,
        )


    @router.post(
        "/{event_id}/not-ready",
        include_in_schema=False,
    )
    async def event_mark_not_ready(
        request: Request,
        event_id: str,
        csrf_token: str = Form(...),
    ):
        redirect = _require_staff(request)

        if redirect:
            return redirect

        unavailable = _database_unavailable(
            request,
            templates,
        )

        if unavailable:
            return unavailable

        if not _valid_csrf(
            request,
            csrf_token,
        ):
            return RedirectResponse(
                f"/staff/events/{event_id}",
                status_code=303,
            )

        with request.app.state.session_factory() as session:
            event = get_event(
                session,
                event_id,
            )

            if event is None:
                return templates.TemplateResponse(
                    request=request,
                    name="404.html",
                    context={},
                    status_code=404,
                )

            update_event(
                session,
                event,
                event_ready=False,
                event_ready_at=None,
            )

        return RedirectResponse(
            f"/staff/events/{event_id}",
            status_code=303,
        )


    return router
