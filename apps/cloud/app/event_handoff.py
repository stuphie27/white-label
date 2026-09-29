from __future__ import annotations

import hmac
from datetime import date

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select

from app.db.models import Event
from app.db.session import database_schema_is_ready


def _authorise(
    request: Request,
    supplied: str | None,
) -> None:
    configured = str(
        request.app.state.settings.sync_api_key or ""
    ).strip()

    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sync API key is not configured",
        )

    supplied = (supplied or "").strip()

    if not supplied or not hmac.compare_digest(
        configured,
        supplied,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid sync API key",
        )

    engine = getattr(
        request.app.state,
        "engine",
        None,
    )

    if (
        engine is None
        or not database_schema_is_ready(engine)
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database schema is not ready",
        )


def build_event_handoff_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/sync/v1/handoff",
        tags=["event-handoff"],
    )

    @router.get("/events/next")
    async def next_online_event(
        request: Request,
        x_pirouette_sync_key: str | None = Header(
            default=None
        ),
    ):
        """
        Read-only STUPHIE Online -> Offline event handoff.

        STUPHIE Online remains the authoritative event master.

        Only an event explicitly confirmed as Event Ready can
        be handed to the local event system.

        This endpoint does not activate an event and performs
        no database writes.
        """

        _authorise(
            request,
            x_pirouette_sync_key,
        )

        today = date.today()

        with request.app.state.session_factory() as session:
            statement = (
                select(Event)
                .where(
                    Event.status.notin_(
                        ("complete", "archived", "cancelled")
                    ),
                    Event.end_date >= today,
                )
                .order_by(
                    Event.start_date.asc(),
                    Event.created_at.asc(),
                )
                .limit(1)
            )

            event = session.scalar(statement)

            if event is None:
                return {
                    "event": None,
                    "status": "no_ready_event",
                }

            return {
                "event": {
                    "id": event.id,
                    "source_ref": event.source_ref,
                    "name": event.name,
                    "client_name": event.client_name,
                    "principal_name": event.principal_name,
                    "photographer_name": event.photographer_name,
                    "dance_style": event.dance_style,
                    "start_date": event.start_date,
                    "end_date": event.end_date,
                    "arrival_time": event.arrival_time,
                    "photography_start": event.photography_start,
                    "photography_finish": event.photography_finish,
                    "venue": event.venue,
                    "description": event.description,
                    "internal_notes": event.internal_notes,
                    "status": event.status,
                    "promoter_name": event.promoter_name,
                    "promoter_email": event.promoter_email,
                    "promoter_phone": event.promoter_phone,
                    "staff_required": event.staff_required,
                    "event_start_time": event.event_start_time,
                    "event_end_time": event.event_end_time,
                    "travel_time_minutes": event.travel_time_minutes,
                    "hotel_required": event.hotel_required,
                    "hotel_info": event.hotel_info,
                    "planning_notes": event.planning_notes,
                    "photography_enabled": event.photography_enabled,
                    "video_enabled": event.video_enabled,
                    "printing_enabled": event.printing_enabled,
                    "customer_kiosk_enabled": event.customer_kiosk_enabled,
                    "event_ready": event.event_ready,
                    "event_ready_at": event.event_ready_at,
                },
                "status": "ready",
            }

    return router
