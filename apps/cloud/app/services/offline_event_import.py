from __future__ import annotations

from datetime import date, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event

# Retired historical Offline event identities.
#
# These two records pre-date STUPHIE Online becoming the authoritative
# event master. They have already been completed/archived Online and must
# never be re-imported from an old Pirouette Offline database.
#
# Fareham Live       = OFFLINE-EVENT-2
# Park Regis / NATD  = OFFLINE-EVENT-18
IGNORED_LEGACY_OFFLINE_EVENT_REFS = {
    "OFFLINE-EVENT-2",
    "OFFLINE-EVENT-18",
}



def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _date(value: Any) -> date:
    text = _text(value)
    return date.fromisoformat(text)


def _time(value: Any) -> time | None:
    text = _text(value)

    if not text:
        return None

    return time.fromisoformat(text)


def _optional_int(value: Any) -> int | None:
    if value is None or _text(value) == "":
        return None

    return int(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    return _text(value).lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
        "required",
        "needed",
    }


def import_offline_event(
    session: Session,
    payload: dict[str, Any],
) -> tuple[Event, str]:

    source_ref = _text(payload.get("source_ref"))

    if not source_ref.startswith("OFFLINE-EVENT-"):
        raise ValueError(
            f"Invalid Offline Event source_ref: {source_ref!r}"
        )

    event = session.scalar(
        select(Event).where(
            Event.source_ref == source_ref
        )
    )

    # Explicitly reviewed legacy production identities.
    LEGACY_RECONCILIATION = {
        "OFFLINE-EVENT-2": "project-pirouette-event-2",
        "OFFLINE-EVENT-18": "project-pirouette-event-18",
    }

    if event is None and source_ref in LEGACY_RECONCILIATION:
        legacy_source_ref = LEGACY_RECONCILIATION[source_ref]

        candidate = session.scalar(
            select(Event).where(
                Event.source_ref == legacy_source_ref
            )
        )

        if candidate is not None:
            expected_date = _date(payload.get("start_date"))
            expected_venue = _text(
                payload.get("venue")
            ).casefold()

            actual_venue = _text(
                candidate.venue
            ).casefold()

            if (
                candidate.start_date != expected_date
                or actual_venue != expected_venue
            ):
                raise ValueError(
                    "Legacy reconciliation safety check failed "
                    f"for {source_ref}: {legacy_source_ref}"
                )

            event = candidate

    action = "updated"

    if event is None:
        event = Event(
            source_ref=source_ref,
            name=_text(payload.get("name")),
            start_date=_date(payload.get("start_date")),
            end_date=_date(payload.get("end_date")),
        )

        session.add(event)
        action = "created"

    event.name = _text(payload.get("name"))
    event.start_date = _date(payload.get("start_date"))
    event.end_date = _date(payload.get("end_date"))
    event.venue = _text(payload.get("venue"))

    event.promoter_name = _text(
        payload.get("promoter_name")
    )
    event.promoter_email = _text(
        payload.get("promoter_email")
    )
    event.promoter_phone = _text(
        payload.get("promoter_phone")
    )

    event.dance_style = _text(
        payload.get("dance_style")
    )

    event.staff_required = _optional_int(
        payload.get("staff_required")
    )

    event.event_start_time = _time(
        payload.get("event_start_time")
    )
    event.event_end_time = _time(
        payload.get("event_end_time")
    )

    event.travel_time_minutes = _optional_int(
        payload.get("travel_time_minutes")
    )

    event.hotel_required = _bool(
        payload.get("hotel_required")
    )
    event.hotel_info = _text(
        payload.get("hotel_info")
    )

    event.planning_notes = _text(
        payload.get("planning_notes")
    )

    event.event_ready = _bool(
        payload.get("event_ready")
    )

    event.personal_video_enabled = _bool(
        payload.get("personal_video_enabled")
    )
    event.personal_video_type = _text(
        payload.get("personal_video_type")
    )

    # Imported Offline events are planning/master records.
    # Never infer operational completion state from Offline.
    if action == "created":
        event.status = "draft"
        event.client_name = ""
        event.principal_name = ""
        event.photographer_name = ""

    return event, action


def import_offline_events(
    session: Session,
    payloads: list[dict[str, Any]],
) -> dict[str, int]:

    created = 0
    updated = 0

    seen: set[str] = set()

    for payload in payloads:

        source_ref = _text(
            payload.get("source_ref")
        )

        # These retired legacy identities must never be allowed to
        # recreate or update completed historical Online events.
        if source_ref in IGNORED_LEGACY_OFFLINE_EVENT_REFS:
            continue

        if source_ref in seen:
            raise ValueError(
                f"Duplicate source_ref in manifest: "
                f"{source_ref}"
            )

        seen.add(source_ref)

        _, action = import_offline_event(
            session,
            payload,
        )

        if action == "created":
            created += 1
        else:
            updated += 1

    session.commit()

    return {
        "total": len(payloads),
        "created": created,
        "updated": updated,
    }
