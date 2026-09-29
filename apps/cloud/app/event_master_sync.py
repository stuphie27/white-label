from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select

from app.db.models import Event, Gallery


LOGGER = logging.getLogger(__name__)


def fetch_ready_stuphie_events(settings) -> list[dict]:
    base_url = str(
        settings.stuphie_base_url or ""
    ).strip().rstrip("/")

    api_key = str(
        settings.stuphie_sync_api_key or ""
    ).strip()

    if not base_url:
        raise RuntimeError(
            "Stuphie base URL is not configured"
        )

    if not api_key:
        raise RuntimeError(
            "Sync API key is not configured"
        )

    request = Request(
        base_url + "/api/sync/v1/handoff/events/directory",
        method="GET",
        headers={
            "X-Pirouette-Sync-Key": api_key,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(
            request,
            timeout=10,
        ) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(
            "Unable to read Stuphie Event Master feed"
        ) from exc

    events = payload.get("events", [])

    if not isinstance(events, list):
        raise RuntimeError(
            "Invalid Stuphie Event Master response"
        )

    return events


def _parse_date(value) -> date | None:
    clean = str(value or "").strip()

    if not clean:
        return None

    try:
        return date.fromisoformat(clean[:10])
    except ValueError:
        return None


def apply_stuphie_event_master(
    session,
    payload: dict,
) -> Event | None:
    master_source_ref = str(
        payload.get("source_ref") or ""
    ).strip()

    name = str(
        payload.get("name") or ""
    ).strip()

    start_date = _parse_date(
        payload.get("start_date")
    )

    end_date = _parse_date(
        payload.get("end_date")
    ) or start_date

    if (
        not master_source_ref
        or not name
        or start_date is None
        or end_date is None
    ):
        return None

    event = session.scalar(
        select(Event).where(
            Event.master_source_ref == master_source_ref
        )
    )

    if event is None:
        # A Stuphie event can exist online before an event iMac
        # has ever opened it. source_ref deliberately remains NULL
        # until Pirouette establishes the operational Cloud identity.
        event = Event(
            master_source_ref=master_source_ref,
            source_ref=None,
            name=name,
            start_date=start_date,
            end_date=end_date,
            status="draft",
        )
        session.add(event)

    # STUPHIE-owned customer-facing master information.
    event.name = name
    event.start_date = start_date
    event.end_date = end_date

    event.venue = str(
        payload.get("venue") or ""
    ).strip()

    event.description = str(
        payload.get("description") or ""
    )
    event.brand_id = str(
        payload.get("brand_id") or "sophies"
    ).strip() or "sophies"

    event.event_logo_storage_path = str(
        payload.get("event_logo_storage_path")
        or ""
    ).strip()

    gallery_access_code_hash = str(
        payload.get("gallery_access_code_hash")
        or ""
    ).strip()

    event.gallery_access_code_hash = (
        gallery_access_code_hash
    )

    session.flush()

    galleries = list(
        session.scalars(
            select(Gallery).where(
                Gallery.event_id == event.id
            )
        )
    )

    for gallery in galleries:
        gallery.access_code_hash = (
            gallery_access_code_hash
        )

        if gallery_access_code_hash:
            gallery.visibility = "private"

    session.commit()
    session.refresh(event)

    return event


def refresh_stuphie_event_master(
    session_factory,
    settings,
) -> dict:
    events = fetch_ready_stuphie_events(
        settings
    )

    accepted = 0
    skipped = 0

    with session_factory() as session:
        for payload in events:
            if not isinstance(payload, dict):
                skipped += 1
                continue

            event = apply_stuphie_event_master(
                session,
                payload,
            )

            if event is None:
                skipped += 1
            else:
                accepted += 1

    return {
        "accepted": accepted,
        "skipped": skipped,
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


async def event_master_refresh_loop(
    session_factory,
    settings,
    *,
    interval_seconds: int = 300,
) -> None:
    """Refresh Stuphie Event Master data without affecting customer availability."""
    import asyncio

    delay = max(60, int(interval_seconds))

    while True:
        try:
            await asyncio.to_thread(
                refresh_stuphie_event_master,
                session_factory,
                settings,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Stuphie Event Master refresh failed; "
                "existing Customer Cloud event data remains in use"
            )

        await asyncio.sleep(delay)
