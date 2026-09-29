from __future__ import annotations

from datetime import date, time
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Event, EventStaffAssignment, EventStaffShift, StaffMember, EventAvailabilityRequest

EVENT_STATUSES = ("draft", "live", "processing", "complete", "archived", "cancelled")


def list_events(
    session: Session,
    *,
    query: str = "",
    status: str = "",
    include_archived: bool = True,
) -> list[Event]:
    statement = select(Event)
    clean_query = query.strip()
    if clean_query:
        pattern = f"%{clean_query}%"
        statement = statement.where(
            or_(
                Event.name.ilike(pattern),
                Event.venue.ilike(pattern),
                Event.client_name.ilike(pattern),
                Event.photographer_name.ilike(pattern),
                Event.promoter_name.ilike(pattern),
            )
        )
    if status in EVENT_STATUSES:
        statement = statement.where(Event.status == status)

    if not include_archived:
        statement = statement.where(
            Event.status.notin_(("archived", "cancelled")),
            Event.end_date >= date.today(),
        )
    statement = statement.order_by(Event.start_date.asc(), Event.created_at.asc())
    return list(session.scalars(statement))


def count_events(session: Session, *, status: str | None = None) -> int:
    statement = select(func.count()).select_from(Event)
    if status:
        statement = statement.where(Event.status == status)
    return int(session.scalar(statement) or 0)


def event_status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(Event.status, func.count()).group_by(Event.status)).all()
    counts = {status: 0 for status in EVENT_STATUSES}
    for status, total in rows:
        counts[str(status)] = int(total)
    return counts


def upcoming_events(session: Session, *, limit: int = 5) -> list[Event]:
    statement = (
        select(Event)
        .where(Event.end_date >= date.today(), Event.status != "archived")
        .order_by(Event.start_date.asc(), Event.created_at.asc())
        .limit(limit)
    )
    return list(session.scalars(statement))


def recent_events(session: Session, *, limit: int = 5) -> list[Event]:
    statement = select(Event).order_by(Event.updated_at.desc()).limit(limit)
    return list(session.scalars(statement))


def get_event(session: Session, event_id: str) -> Event | None:
    return session.get(Event, event_id)


def create_event(
    session: Session,
    *,
    name: str,
    client_name: str,
    principal_name: str,
    photographer_name: str,
    dance_style: str,
    start_date: date,
    end_date: date,
    arrival_time: time | None,
    photography_start: time | None,
    photography_finish: time | None,
    venue: str,
    description: str,
    internal_notes: str,
    status: str,
    promoter_name: str = "",
    promoter_email: str = "",
    promoter_phone: str = "",
    staff_required: int | None = None,
    event_start_time: time | None = None,
    event_end_time: time | None = None,
    travel_time_minutes: int | None = None,
    hotel_required: bool = False,
    hotel_info: str = "",
    planning_notes: str = "",
    photography_enabled: bool = True,
    video_enabled: bool = False,
    printing_enabled: bool = True,
    customer_kiosk_enabled: bool = True,
    event_ready: bool = False,
) -> Event:
    event_id = str(uuid4())

    event = Event(
        id=event_id,
        source_ref=f"STUPHIE-EVENT-{event_id}",
        name=name,
        client_name=client_name,
        principal_name=principal_name,
        photographer_name=photographer_name,
        dance_style=dance_style,
        start_date=start_date,
        end_date=end_date,
        arrival_time=arrival_time,
        photography_start=photography_start,
        photography_finish=photography_finish,
        venue=venue,
        description=description,
        internal_notes=internal_notes,
        status=status,
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
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def duplicate_event(session: Session, event: Event) -> Event:
    copy_id = str(uuid4())

    copy = Event(
        id=copy_id,
        source_ref=f"STUPHIE-EVENT-{copy_id}",
        name=f"{event.name} (Copy)",
        client_name=event.client_name,
        principal_name=event.principal_name,
        photographer_name=event.photographer_name,
        dance_style=event.dance_style,
        start_date=event.start_date,
        end_date=event.end_date,
        arrival_time=event.arrival_time,
        photography_start=event.photography_start,
        photography_finish=event.photography_finish,
        venue=event.venue,
        description=event.description,
        internal_notes=event.internal_notes,

        promoter_name=event.promoter_name,
        promoter_email=event.promoter_email,
        promoter_phone=event.promoter_phone,
        staff_required=event.staff_required,
        event_start_time=event.event_start_time,
        event_end_time=event.event_end_time,
        travel_time_minutes=event.travel_time_minutes,
        hotel_required=event.hotel_required,
        hotel_info=event.hotel_info,
        planning_notes=event.planning_notes,
        photography_enabled=event.photography_enabled,
        video_enabled=event.video_enabled,
        printing_enabled=event.printing_enabled,
        customer_kiosk_enabled=event.customer_kiosk_enabled,

        event_ready=False,
        event_ready_at=None,
        package_version=0,
        package_state="not_published",
        package_published_at=None,
        package_published_by="",

        status="draft",
        booking_confirmed=False,
        contract_received=False,
        photographer_assigned=False,
        equipment_packed=False,
        galleries_uploaded=False,
        gallery_published=False,
        orders_complete=False,
    )
    session.add(copy)
    session.commit()
    session.refresh(copy)
    return copy


def update_event(session: Session, event: Event, **values: object) -> Event:
    for key, value in values.items():
        setattr(event, key, value)
    session.commit()
    session.refresh(event)
    return event


def list_active_staff(session: Session) -> list[StaffMember]:
    statement = (
        select(StaffMember)
        .where(StaffMember.active.is_(True))
        .order_by(StaffMember.staff_name.asc(), StaffMember.preferred_name.asc())
    )
    return list(session.scalars(statement))


def event_staff_availability(
    session: Session,
    *,
    event: Event,
    staff_members: list[StaffMember],
) -> dict[str, str]:
    """
    Return availability for Staff Master members for one event.

    Availability is joined through the existing shared identities:

        Event.source_ref
        EventAvailabilityRequest.event_source_ref

    and:

        StaffMember.staff_source_ref
        EventAvailabilityRequest.staff_source_ref

    Existing availability values are preserved:
        available
        unavailable
        unknown
    """

    if not staff_members:
        return {}

    event_source_ref = str(
        getattr(event, "source_ref", "") or ""
    ).strip()

    if not event_source_ref:
        return {
            staff.id: "unknown"
            for staff in staff_members
        }

    staff_refs = {
        str(staff.staff_source_ref or "").strip(): staff.id
        for staff in staff_members
        if str(staff.staff_source_ref or "").strip()
    }

    if not staff_refs:
        return {
            staff.id: "unknown"
            for staff in staff_members
        }

    statement = select(EventAvailabilityRequest).where(
        EventAvailabilityRequest.event_source_ref == event_source_ref,
        EventAvailabilityRequest.staff_source_ref.in_(
            list(staff_refs.keys())
        ),
    )

    rows = list(session.scalars(statement))

    result = {
        staff.id: "unknown"
        for staff in staff_members
    }

    for row in rows:
        staff_id = staff_refs.get(
            str(row.staff_source_ref or "").strip()
        )

        if not staff_id:
            continue

        status = str(
            row.availability_status or "unknown"
        ).strip().lower()

        if status not in {
            "available",
            "unavailable",
        }:
            status = "unknown"

        result[staff_id] = status

    return result


def list_event_staff_assignments(
    session: Session,
    event_id: str,
) -> list[tuple[EventStaffAssignment, StaffMember]]:
    statement = (
        select(EventStaffAssignment, StaffMember)
        .join(
            StaffMember,
            StaffMember.id == EventStaffAssignment.staff_member_id,
        )
        .where(EventStaffAssignment.event_id == event_id)
        .order_by(
            StaffMember.staff_name.asc(),
            StaffMember.preferred_name.asc(),
        )
    )
    return list(session.execute(statement).all())


def staffing_summary(
    event: Event,
    assignments: list[tuple[EventStaffAssignment, StaffMember]],
) -> dict[str, int]:
    required = max(int(event.staff_required or 0), 0)

    assigned = sum(
        1
        for assignment, _staff in assignments
        if assignment.assignment_status != "cancelled"
    )

    outstanding = max(required - assigned, 0)

    return {
        "required": required,
        "assigned": assigned,
        "outstanding": outstanding,
    }



def event_staff_conflicts(
    session: Session,
    *,
    event: Event,
    staff_members: list[StaffMember],
) -> dict[str, list[Event]]:
    """
    Return overlapping events for each member of Staff Master.

    Only real active assignments are considered. Archived events,
    cancelled assignments and the event currently being viewed are ignored.
    """
    if not staff_members:
        return {}

    staff_ids = [staff.id for staff in staff_members]

    statement = (
        select(EventStaffAssignment, Event)
        .join(
            Event,
            Event.id == EventStaffAssignment.event_id,
        )
        .where(
            EventStaffAssignment.staff_member_id.in_(staff_ids),
            EventStaffAssignment.event_id != event.id,
            EventStaffAssignment.assignment_status != "cancelled",
            Event.status != "archived",
            Event.start_date <= event.end_date,
            Event.end_date >= event.start_date,
        )
        .order_by(
            Event.start_date.asc(),
            Event.name.asc(),
        )
    )

    conflicts: dict[str, list[Event]] = {
        staff.id: []
        for staff in staff_members
    }

    for assignment, other_event in session.execute(statement).all():
        conflicts.setdefault(
            assignment.staff_member_id,
            [],
        ).append(other_event)

    return conflicts


def event_readiness(
    event: Event,
    assignments: list[tuple[EventStaffAssignment, StaffMember]],
    availability: dict[str, str],
    conflicts: dict[str, list[Event]] | None = None,
) -> dict[str, object]:
    """
    Calculate whether an event is genuinely ready.

    This mirrors the mature Offline planning rules while keeping the
    Online Event Master authoritative.
    """
    missing: list[str] = []

    if not str(event.name or "").strip():
        missing.append("Event name")

    if not str(event.venue or "").strip():
        missing.append("Location")

    if not str(event.dance_style or "").strip():
        missing.append("Event style")

    required = max(int(event.staff_required or 0), 0)

    if required <= 0:
        missing.append("Staff required")

    if event.event_start_time is None:
        missing.append("Start time")

    if event.event_end_time is None:
        missing.append("End time")

    if not str(event.promoter_name or "").strip():
        missing.append("Promoter name")

    if (
        not str(event.promoter_email or "").strip()
        and not str(event.promoter_phone or "").strip()
    ):
        missing.append("Promoter contact")

    if event.travel_time_minutes is None:
        missing.append("Travel time")

    # Online stores hotel requirement as a deliberate yes/no switch.
    # "Not required" is therefore complete; a required hotel needs details.
    if event.hotel_required and not str(event.hotel_info or "").strip():
        missing.append("Hotel information")

    active_assignments = [
        (assignment, staff)
        for assignment, staff in assignments
        if assignment.assignment_status != "cancelled"
    ]

    effective_assigned = sum(
        1
        for _assignment, staff in active_assignments
        if availability.get(staff.id, "unknown") != "unavailable"
    )

    unavailable_assigned = sum(
        1
        for _assignment, staff in active_assignments
        if availability.get(staff.id, "unknown") == "unavailable"
    )

    shortfall = (
        max(required - effective_assigned, 0)
        if required > 0
        else 0
    )

    if shortfall:
        missing.append(
            f"Staffing shortfall ({shortfall})"
        )

    conflict_map = conflicts or {}

    assigned_staff_ids = {
        staff.id
        for assignment, staff in active_assignments
    }

    conflict_count = sum(
        1
        for staff_id in assigned_staff_ids
        if conflict_map.get(staff_id)
    )

    complete = not missing
    confirmed = bool(event.event_ready)

    return {
        "missing": missing,
        "complete": complete,
        "confirmed": confirmed,
        "ready": confirmed and complete,
        "needs_attention": bool(missing),
        "required": required,
        "assigned": len(active_assignments),
        "effective_assigned": effective_assigned,
        "unavailable_assigned": unavailable_assigned,
        "shortfall": shortfall,
        "conflict_count": conflict_count,
    }


def assign_staff_to_event(
    session: Session,
    *,
    event: Event,
    staff_member: StaffMember,
    role: str = "",
    notes: str = "",
) -> EventStaffAssignment:
    existing = session.scalar(
        select(EventStaffAssignment).where(
            EventStaffAssignment.event_id == event.id,
            EventStaffAssignment.staff_member_id == staff_member.id,
        )
    )

    clean_role = role.strip() or staff_member.default_role or "Photography Staff"

    if existing is not None:
        existing.role = clean_role
        existing.assignment_status = "assigned"
        existing.notes = notes.strip()

        session.commit()
        session.refresh(existing)
        return existing

    assignment = EventStaffAssignment(
        event_id=event.id,
        staff_member_id=staff_member.id,
        role=clean_role,
        assignment_status="assigned",
        notes=notes.strip(),
    )

    session.add(assignment)
    session.commit()
    session.refresh(assignment)

    return assignment


def remove_staff_from_event(
    session: Session,
    *,
    event_id: str,
    staff_member_id: str,
) -> bool:
    assignment = session.scalar(
        select(EventStaffAssignment).where(
            EventStaffAssignment.event_id == event_id,
            EventStaffAssignment.staff_member_id == staff_member_id,
        )
    )

    if assignment is None:
        return False

    session.delete(assignment)
    session.commit()

    return True


def list_event_staff_shifts(
    session: Session,
    event_id: str,
) -> list[EventStaffShift]:
    statement = (
        select(EventStaffShift)
        .where(
            EventStaffShift.event_id == event_id
        )
        .order_by(
            EventStaffShift.shift_date.asc(),
            EventStaffShift.scheduled_start.asc(),
            EventStaffShift.created_at.asc(),
        )
    )

    return list(session.scalars(statement))


def event_staff_shift_map(
    shifts: list[EventStaffShift],
) -> dict[str, list[EventStaffShift]]:
    result: dict[str, list[EventStaffShift]] = {}

    for shift in shifts:
        result.setdefault(
            shift.assignment_id,
            [],
        ).append(shift)

    return result


def get_event_staff_assignment(
    session: Session,
    *,
    event_id: str,
    staff_member_id: str,
) -> EventStaffAssignment | None:
    return session.scalar(
        select(EventStaffAssignment).where(
            EventStaffAssignment.event_id == event_id,
            EventStaffAssignment.staff_member_id
            == staff_member_id,
        )
    )


def save_event_staff_shift(
    session: Session,
    *,
    event: Event,
    assignment: EventStaffAssignment,
    shift_date: date,
    scheduled_start: time | None,
    scheduled_finish: time | None,
    break_minutes: int = 0,
) -> EventStaffShift:
    if assignment.event_id != event.id:
        raise ValueError(
            "Staff assignment does not belong to this event."
        )

    if (
        scheduled_start
        and scheduled_finish
        and scheduled_finish < scheduled_start
    ):
        raise ValueError(
            "Scheduled finish cannot be before scheduled start."
        )

    clean_break = max(int(break_minutes or 0), 0)

    existing = session.scalar(
        select(EventStaffShift).where(
            EventStaffShift.assignment_id == assignment.id,
            EventStaffShift.shift_date == shift_date,
        )
    )

    if existing is not None:
        existing.role = assignment.role
        existing.scheduled_start = scheduled_start
        existing.scheduled_finish = scheduled_finish
        existing.break_minutes = clean_break

        if existing.actual_clock_in is None:
            existing.status = "scheduled"

        session.commit()
        session.refresh(existing)
        return existing

    shift = EventStaffShift(
        event_id=event.id,
        assignment_id=assignment.id,
        staff_member_id=assignment.staff_member_id,
        shift_date=shift_date,
        role=assignment.role,
        scheduled_start=scheduled_start,
        scheduled_finish=scheduled_finish,
        break_minutes=clean_break,
        status="scheduled",
    )

    session.add(shift)
    session.commit()
    session.refresh(shift)

    return shift


def get_event_staff_shift(
    session: Session,
    *,
    event_id: str,
    staff_member_id: str,
    shift_id: str,
) -> EventStaffShift | None:
    return session.scalar(
        select(EventStaffShift).where(
            EventStaffShift.id == shift_id,
            EventStaffShift.event_id == event_id,
            EventStaffShift.staff_member_id == staff_member_id,
        )
    )


def clock_in_event_staff_shift(
    session: Session,
    *,
    shift: EventStaffShift,
) -> EventStaffShift:
    from datetime import datetime, timezone

    if shift.actual_clock_out is not None:
        raise ValueError("Completed shift cannot be clocked in.")

    if shift.actual_clock_in is None:
        shift.actual_clock_in = datetime.now(timezone.utc)

    shift.status = "working"

    session.commit()
    session.refresh(shift)

    return shift


def clock_out_event_staff_shift(
    session: Session,
    *,
    shift: EventStaffShift,
) -> EventStaffShift:
    from datetime import datetime, timezone

    if shift.actual_clock_in is None:
        raise ValueError("Shift must be clocked in first.")

    if shift.actual_clock_out is None:
        shift.actual_clock_out = datetime.now(timezone.utc)

    shift.status = "complete"

    session.commit()
    session.refresh(shift)

    return shift


def event_staff_shift_worked_minutes(
    shift: EventStaffShift,
) -> int | None:
    if (
        shift.actual_clock_in is None
        or shift.actual_clock_out is None
    ):
        return None

    elapsed = shift.actual_clock_out - shift.actual_clock_in
    minutes = int(elapsed.total_seconds() // 60)

    return max(
        minutes - max(int(shift.break_minutes or 0), 0),
        0,
    )



PRODUCTION_CHECKLIST = (
    ("booking_confirmed", "Booking confirmed"),
    ("contract_received", "Contract received"),
    ("photographer_assigned", "Photographer assigned"),
    ("equipment_packed", "Equipment packed"),
    ("galleries_uploaded", "Galleries uploaded"),
    ("gallery_published", "Gallery published"),
    ("orders_complete", "Orders complete"),
)


def checklist_progress(event: Event) -> tuple[int, int, int]:
    completed = sum(1 for field, _label in PRODUCTION_CHECKLIST if bool(getattr(event, field)))
    total = len(PRODUCTION_CHECKLIST)
    percentage = round((completed / total) * 100) if total else 0
    return completed, total, percentage


def production_summary(session: Session) -> dict[str, int]:
    events = list(session.scalars(select(Event).where(Event.status != "archived")))
    ready = 0
    attention = 0
    for event in events:
        completed, total, _percentage = checklist_progress(event)
        if completed == total:
            ready += 1
        elif event.start_date >= date.today():
            attention += 1
    return {"ready": ready, "attention": attention}
