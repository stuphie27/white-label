from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    Event,
    PersonalVideoBooking,
    PersonalVideoEntry,
)
from app.main import create_app


def _factory(client):
    return client.app.state.session_factory


def _live_event(client, booking_type: str):
    factory = _factory(client)

    with factory() as session:
        event = session.scalar(
            select(Event).limit(1)
        )

        if event is None:
            event = Event(
                source_ref=f"pv-14d2-{booking_type}",
                name=f"14D2 {booking_type.title()} Event",
                start_date=date(2026, 8, 19),
                end_date=date(2026, 8, 20),
                venue="14D2 Test Venue",
                status="live",
                personal_video_enabled=True,
                personal_video_type=booking_type,
            )
            session.add(event)
        else:
            event.status = "live"
            event.personal_video_enabled = True
            event.personal_video_type = booking_type

        session.commit()

        return event.id


def _latest_booking(client):
    factory = _factory(client)

    with factory() as session:
        return session.scalar(
            select(PersonalVideoBooking)
            .order_by(
                PersonalVideoBooking.created_at.desc()
            )
            .limit(1)
        )


def _entries(client, booking_id: str):
    factory = _factory(client)

    with factory() as session:
        return list(
            session.scalars(
                select(PersonalVideoEntry)
                .where(
                    PersonalVideoEntry.booking_id
                    == booking_id
                )
            )
        )


def test_ballroom_online_booking_is_saved():
    with TestClient(create_app()) as client:
        _live_event(client, "ballroom")

        response = client.post(
            "/personal-video/ballroom",
            data={
                "customer_name": "Alex Dancer",
                "customer_email": "alex@example.com",
                "customer_phone": "07123456789",
                "partner_email": "",
                "competitor_number": "42",
                "outfit_description": "Blue dress",
                "minimum_video_count": "2",
                "video_format": "Vertical",
                "ballroom_payment_plan": "half",
                "payment_choice": "online",
                "intended_use": [
                    "Review my dancing / show my teacher"
                ],
                "customer_notes": "Test booking",
                "terms_accepted": "yes",
                "entries_json": json.dumps(
                    [
                        {
                            "event_number": "12",
                            "event_name": "Waltz",
                            "performance_date": "2026-08-19",
                            "performance_time": "",
                            "dancer_number": "42",
                            "expected_video_count": 1,
                            "filming_instruction": "Whole dance",
                            "filming_details": "",
                        },
                        {
                            "event_number": "18",
                            "event_name": "Quickstep",
                            "performance_date": "2026-08-20",
                            "performance_time": "",
                            "dancer_number": "84",
                            "expected_video_count": 1,
                            "filming_instruction": "Whole dance",
                            "filming_details": "",
                        },
                    ]
                ),
            },
        )

        assert response.status_code == 200
        assert "Your booking has been saved" in response.text

        booking = _latest_booking(client)

        assert booking is not None
        assert booking.booking_type == "ballroom"
        assert booking.reference.startswith("SPV-")
        assert booking.manage_token
        assert booking.payment_status == "awaiting_payment"
        assert booking.total_paid_pence == 0
        assert booking.minimum_video_count == 2

        rows = _entries(client, booking.id)

        assert len(rows) == 2

        dancer_numbers = {
            row.dancer_number
            for row in rows
        }

        assert dancer_numbers == {"42", "84"}
        assert all(
            row.performance_date is not None
            for row in rows
        )


def test_freestyle_online_booking_saves_multiple_entries():
    with TestClient(create_app()) as client:
        _live_event(client, "freestyle")

        response = client.post(
            "/personal-video/freestyle",
            data={
                "customer_name": "Parent Name",
                "customer_email": "parent@example.com",
                "customer_phone": "07123456789",
                "payment_choice": "online",
                "entries_json": json.dumps(
                    [
                        {
                            "dancer_name": "Jamie",
                            "dancer_number": "91",
                            "dance_style": "Slow",
                            "category": "Intermediate",
                            "age_group": "U14",
                            "performance_date": "2026-08-19",
                            "performance_time": "",
                        },
                        {
                            "dancer_name": "Morgan",
                            "dancer_number": "37",
                            "dance_style": "Fast",
                            "category": "Starter",
                            "age_group": "U12",
                            "performance_date": "2026-08-20",
                            "performance_time": "14:30",
                        },
                    ]
                ),
            },
        )

        assert response.status_code == 200

        booking = _latest_booking(client)

        assert booking is not None
        assert booking.booking_type == "freestyle"
        assert booking.manage_token
        assert booking.final_total_pence == 4600
        assert booking.total_paid_pence == 0

        rows = _entries(client, booking.id)

        assert len(rows) == 2

        numbers = {
            row.dancer_number
            for row in rows
        }

        assert numbers == {"91", "37"}

        assert all(
            row.performance_date is not None
            for row in rows
        )


def test_theatre_online_booking_saves_multiple_entries():
    with TestClient(create_app()) as client:
        _live_event(client, "theatre")

        response = client.post(
            "/personal-video/theatre",
            data={
                "customer_name": "Taylor Customer",
                "customer_email": "taylor@example.com",
                "customer_phone": "07123456789",
                "payment_choice": "online",
                "entries_json": json.dumps(
                    [
                        {
                            "dancer_name": "Taylor Performer",
                            "dance_name": "My Theatre Dance",
                            "category": "Junior",
                            "event_number": "",
                            "performance_date": "2026-08-19",
                            "performance_time": "",
                        },
                        {
                            "dancer_name": "Taylor Performer",
                            "dance_name": "Finale",
                            "category": "Senior",
                            "event_number": "44",
                            "performance_date": "2026-08-20",
                            "performance_time": "16:10",
                        },
                    ]
                ),
            },
        )

        assert response.status_code == 200

        booking = _latest_booking(client)

        assert booking is not None
        assert booking.booking_type == "theatre"
        assert booking.manage_token
        assert booking.final_total_pence == 4600
        assert booking.total_paid_pence == 0

        rows = _entries(client, booking.id)

        assert len(rows) == 2
        assert rows[0].dancer_name
        assert rows[0].dance_name
        assert rows[0].category
        assert rows[0].performance_date is not None
        assert rows[0].performance_time is None


def test_invalid_freestyle_entry_does_not_write():
    with TestClient(create_app()) as client:
        _live_event(client, "freestyle")

        factory = _factory(client)

        with factory() as session:
            before = len(
                list(
                    session.scalars(
                        select(PersonalVideoBooking)
                    )
                )
            )

        response = client.post(
            "/personal-video/freestyle",
            data={
                "customer_name": "Parent",
                "customer_email": "parent@example.com",
                "customer_phone": "07123456789",
                "payment_choice": "online",
                "entries_json": json.dumps(
                    [
                        {
                            "dancer_name": "Jamie",
                            "dancer_number": "",
                            "dance_style": "Slow",
                            "category": "Intermediate",
                            "age_group": "U14",
                            "performance_date": "2026-08-19",
                        }
                    ]
                ),
            },
        )

        assert response.status_code == 422

        with factory() as session:
            after = len(
                list(
                    session.scalars(
                        select(PersonalVideoBooking)
                    )
                )
            )

        assert after == before
