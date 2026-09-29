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
                source_ref=f"pv-14e-{booking_type}",
                name=f"14E {booking_type.title()} Event",
                start_date=date(2026, 8, 19),
                end_date=date(2026, 8, 20),
                venue="14E Test Venue",
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


def _ballroom_booking(client):
    _live_event(client, "ballroom")

    response = client.post(
        "/personal-video/ballroom",
        data={
            "customer_name": "Original Customer",
            "customer_email": "original@example.com",
            "customer_phone": "07000000000",
            "partner_email": "",
            "competitor_number": "",
            "outfit_description": "",
            "minimum_video_count": "2",
            "video_format": "Vertical",
            "ballroom_payment_plan": "half",
            "payment_choice": "online",
            "intended_use": [
                "Review my dancing / show my teacher"
            ],
            "customer_notes": "",
            "terms_accepted": "yes",
            "entries_json": json.dumps(
                [
                    {
                        "event_number": "",
                        "event_name": "",
                        "performance_date": "2026-08-19",
                        "performance_time": "",
                        "dancer_number": "",
                        "expected_video_count": 1,
                        "filming_instruction": "Whole dance",
                        "filming_details": "",
                    },
                    {
                        "event_number": "",
                        "event_name": "",
                        "performance_date": "2026-08-20",
                        "performance_time": "",
                        "dancer_number": "",
                        "expected_video_count": 1,
                        "filming_instruction": "Whole dance",
                        "filming_details": "",
                    },
                ]
            ),
        },
    )

    assert response.status_code == 200

    factory = _factory(client)

    with factory() as session:
        booking = session.scalar(
            select(PersonalVideoBooking)
            .where(
                PersonalVideoBooking.booking_type
                == "ballroom"
            )
            .order_by(
                PersonalVideoBooking.created_at.desc()
            )
            .limit(1)
        )

        assert booking is not None

        return {
            "id": booking.id,
            "token": booking.manage_token,
            "reference": booking.reference,
            "initial_payment_pence": (
                booking.initial_payment_pence
            ),
            "final_total_pence": (
                booking.final_total_pence
            ),
            "minimum_video_count": (
                booking.minimum_video_count
            ),
            "video_format": booking.video_format,
            "terms_version": booking.terms_version,
        }


def test_saved_page_contains_private_manage_link():
    with TestClient(create_app()) as client:
        _live_event(client, "freestyle")

        response = client.post(
            "/personal-video/freestyle",
            data={
                "customer_name": "Parent Name",
                "customer_email": "parent@example.com",
                "customer_phone": "",
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
                        }
                    ]
                ),
            },
        )

        assert response.status_code == 200
        assert "Manage My Booking" in response.text
        assert "/personal-video/manage/" in response.text


def test_manage_page_requires_valid_private_token():
    with TestClient(create_app()) as client:
        response = client.get(
            "/personal-video/manage/not-a-real-token"
        )

        assert response.status_code == 404


def test_customer_can_update_manageable_booking_fields():
    with TestClient(create_app()) as client:
        original = _ballroom_booking(client)

        response = client.post(
            f"/personal-video/manage/{original['token']}",
            data={
                "customer_name": "Updated Customer",
                "customer_email": "updated@example.com",
                "customer_phone": "07111111111",
                "customer_notes": "Updated remotely",
                "competitor_number": "COMP-99",
                "outfit_description": "Red dress",
            },
        )

        assert response.status_code == 200
        assert "Booking details updated" in response.text

        factory = _factory(client)

        with factory() as session:
            booking = session.get(
                PersonalVideoBooking,
                original["id"],
            )

            assert booking is not None
            assert (
                booking.customer_name
                == "Updated Customer"
            )
            assert (
                booking.customer_email
                == "updated@example.com"
            )
            assert (
                booking.customer_phone
                == "07111111111"
            )
            assert (
                booking.customer_notes
                == "Updated remotely"
            )
            assert (
                booking.competitor_number
                == "COMP-99"
            )
            assert (
                booking.outfit_description
                == "Red dress"
            )

            # Protected fields must remain unchanged.
            assert (
                booking.initial_payment_pence
                == original["initial_payment_pence"]
            )
            assert (
                booking.final_total_pence
                == original["final_total_pence"]
            )
            assert (
                booking.minimum_video_count
                == original["minimum_video_count"]
            )
            assert (
                booking.video_format
                == original["video_format"]
            )
            assert (
                booking.terms_version
                == original["terms_version"]
            )


def test_invalid_manage_update_does_not_change_booking():
    with TestClient(create_app()) as client:
        original = _ballroom_booking(client)

        response = client.post(
            f"/personal-video/manage/{original['token']}",
            data={
                "customer_name": "",
                "customer_email": "not-an-email",
                "customer_phone": "07111111111",
                "customer_notes": "Should not save",
                "competitor_number": "BAD",
                "outfit_description": "Bad",
            },
        )

        assert response.status_code == 422

        factory = _factory(client)

        with factory() as session:
            booking = session.get(
                PersonalVideoBooking,
                original["id"],
            )

            assert booking is not None
            assert (
                booking.customer_name
                == "Original Customer"
            )
            assert (
                booking.customer_email
                == "original@example.com"
            )


def test_ballroom_event_identifiers_can_be_added_later():
    with TestClient(create_app()) as client:
        original = _ballroom_booking(client)

        factory = _factory(client)

        with factory() as session:
            entries = list(
                session.scalars(
                    select(PersonalVideoEntry)
                    .where(
                        PersonalVideoEntry.booking_id
                        == original["id"]
                    )
                    .order_by(
                        PersonalVideoEntry.performance_date
                    )
                )
            )

            assert len(entries) == 2

            first_id = entries[0].id
            second_id = entries[1].id

        first = client.post(
            (
                f"/personal-video/manage/"
                f"{original['token']}/entry/{first_id}"
            ),
            data={
                "event_number": "12",
                "event_name": "Waltz",
                "dancer_number": "42",
            },
        )

        assert first.status_code == 200
        assert "Event details saved" in first.text

        second = client.post(
            (
                f"/personal-video/manage/"
                f"{original['token']}/entry/{second_id}"
            ),
            data={
                "event_number": "18",
                "event_name": "Quickstep",
                "dancer_number": "84",
            },
        )

        assert second.status_code == 200

        with factory() as session:
            first_entry = session.get(
                PersonalVideoEntry,
                first_id,
            )
            second_entry = session.get(
                PersonalVideoEntry,
                second_id,
            )

            assert first_entry is not None
            assert second_entry is not None

            assert first_entry.event_number == "12"
            assert first_entry.event_name == "Waltz"
            assert first_entry.dancer_number == "42"

            assert second_entry.event_number == "18"
            assert second_entry.event_name == "Quickstep"
            assert second_entry.dancer_number == "84"


def test_entry_cannot_be_edited_with_another_booking_token():
    with TestClient(create_app()) as client:
        first = _ballroom_booking(client)
        second = _ballroom_booking(client)

        factory = _factory(client)

        with factory() as session:
            target = session.scalar(
                select(PersonalVideoEntry)
                .where(
                    PersonalVideoEntry.booking_id
                    == first["id"]
                )
                .limit(1)
            )

            assert target is not None
            target_id = target.id

        response = client.post(
            (
                f"/personal-video/manage/"
                f"{second['token']}/entry/{target_id}"
            ),
            data={
                "event_number": "HACK",
                "event_name": "Wrong",
                "dancer_number": "999",
            },
        )

        assert response.status_code == 404

        with factory() as session:
            target = session.get(
                PersonalVideoEntry,
                target_id,
            )

            assert target is not None
            assert target.event_number != "HACK"




def _booking_and_entry(client, booking_type: str):
    factory = _factory(client)

    with factory() as session:
        booking = session.scalar(
            select(PersonalVideoBooking)
            .where(
                PersonalVideoBooking.booking_type
                == booking_type
            )
            .order_by(
                PersonalVideoBooking.created_at.desc()
            )
            .limit(1)
        )

        assert booking is not None

        entry = session.scalar(
            select(PersonalVideoEntry)
            .where(
                PersonalVideoEntry.booking_id
                == booking.id
            )
            .order_by(
                PersonalVideoEntry.created_at
            )
            .limit(1)
        )

        assert entry is not None

        return {
            "booking_id": booking.id,
            "entry_id": entry.id,
            "token": booking.manage_token,
            "final_total_pence": booking.final_total_pence,
            "unit_price_pence": entry.unit_price_pence,
            "expected_video_count": (
                entry.expected_video_count
            ),
        }


def test_freestyle_performance_can_be_corrected_remotely():
    with TestClient(create_app()) as client:
        _live_event(client, "freestyle")

        created = client.post(
            "/personal-video/freestyle",
            data={
                "customer_name": "Parent",
                "customer_email": "parent@example.com",
                "customer_phone": "",
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
                        }
                    ]
                ),
            },
        )

        assert created.status_code == 200

        original = _booking_and_entry(
            client,
            "freestyle",
        )

        updated = client.post(
            (
                f"/personal-video/manage/"
                f"{original['token']}/entry/"
                f"{original['entry_id']}"
            ),
            data={
                "dancer_name": "Jamie Updated",
                "dancer_number": "123",
                "dance_style": "Fast",
                "category": "Starter",
                "age_group": "U16",
                "performance_date": "2026-08-20",
                "performance_time": "15:45",
            },
        )

        assert updated.status_code == 200
        assert (
            "Performance details saved"
            in updated.text
        )

        factory = _factory(client)

        with factory() as session:
            booking = session.get(
                PersonalVideoBooking,
                original["booking_id"],
            )
            entry = session.get(
                PersonalVideoEntry,
                original["entry_id"],
            )

            assert booking is not None
            assert entry is not None

            assert entry.dancer_name == "Jamie Updated"
            assert entry.dancer_number == "123"
            assert entry.dance_style == "Fast"
            assert entry.category == "Starter"
            assert entry.age_group == "U16"
            assert (
                entry.performance_date.isoformat()
                == "2026-08-20"
            )
            assert (
                entry.performance_time.strftime("%H:%M")
                == "15:45"
            )

            assert (
                booking.final_total_pence
                == original["final_total_pence"]
            )
            assert (
                entry.unit_price_pence
                == original["unit_price_pence"]
            )
            assert (
                entry.expected_video_count
                == original["expected_video_count"]
            )


def test_invalid_freestyle_manage_update_does_not_save():
    with TestClient(create_app()) as client:
        _live_event(client, "freestyle")

        created = client.post(
            "/personal-video/freestyle",
            data={
                "customer_name": "Parent",
                "customer_email": "parent@example.com",
                "customer_phone": "",
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
                        }
                    ]
                ),
            },
        )

        assert created.status_code == 200

        original = _booking_and_entry(
            client,
            "freestyle",
        )

        response = client.post(
            (
                f"/personal-video/manage/"
                f"{original['token']}/entry/"
                f"{original['entry_id']}"
            ),
            data={
                "dancer_name": "Should Not Save",
                "dancer_number": "",
                "dance_style": "Fast",
                "category": "Starter",
                "age_group": "U16",
                "performance_date": "2026-08-20",
                "performance_time": "",
            },
        )

        assert response.status_code == 422

        factory = _factory(client)

        with factory() as session:
            entry = session.get(
                PersonalVideoEntry,
                original["entry_id"],
            )

            assert entry is not None
            assert entry.dancer_name == "Jamie"
            assert entry.dancer_number == "91"


def test_theatre_performance_can_be_corrected_remotely():
    with TestClient(create_app()) as client:
        _live_event(client, "theatre")

        created = client.post(
            "/personal-video/theatre",
            data={
                "customer_name": "Customer",
                "customer_email": "customer@example.com",
                "customer_phone": "07123456789",
                "payment_choice": "online",
                "entries_json": json.dumps(
                    [
                        {
                            "dancer_name": "Performer",
                            "dance_name": "Original Dance",
                            "category": "Junior",
                            "event_number": "",
                            "performance_date": "2026-08-19",
                            "performance_time": "",
                        }
                    ]
                ),
            },
        )

        assert created.status_code == 200

        original = _booking_and_entry(
            client,
            "theatre",
        )

        response = client.post(
            (
                f"/personal-video/manage/"
                f"{original['token']}/entry/"
                f"{original['entry_id']}"
            ),
            data={
                "dancer_name": "Updated Performer",
                "dance_name": "Updated Dance",
                "category": "Senior",
                "event_number": "44",
                "performance_date": "2026-08-20",
                "performance_time": "",
            },
        )

        assert response.status_code == 200

        factory = _factory(client)

        with factory() as session:
            booking = session.get(
                PersonalVideoBooking,
                original["booking_id"],
            )
            entry = session.get(
                PersonalVideoEntry,
                original["entry_id"],
            )

            assert booking is not None
            assert entry is not None

            assert (
                entry.dancer_name
                == "Updated Performer"
            )
            assert entry.dance_name == "Updated Dance"
            assert entry.category == "Senior"
            assert entry.event_number == "44"
            assert (
                entry.performance_date.isoformat()
                == "2026-08-20"
            )
            assert entry.performance_time is None

            assert (
                booking.final_total_pence
                == original["final_total_pence"]
            )
            assert (
                entry.unit_price_pence
                == original["unit_price_pence"]
            )
            assert (
                entry.expected_video_count
                == original["expected_video_count"]
            )


def test_theatre_manage_requires_core_performance_fields():
    with TestClient(create_app()) as client:
        _live_event(client, "theatre")

        created = client.post(
            "/personal-video/theatre",
            data={
                "customer_name": "Customer",
                "customer_email": "customer@example.com",
                "customer_phone": "07123456789",
                "payment_choice": "online",
                "entries_json": json.dumps(
                    [
                        {
                            "dancer_name": "Performer",
                            "dance_name": "Dance",
                            "category": "Junior",
                            "event_number": "",
                            "performance_date": "2026-08-19",
                            "performance_time": "",
                        }
                    ]
                ),
            },
        )

        assert created.status_code == 200

        original = _booking_and_entry(
            client,
            "theatre",
        )

        response = client.post(
            (
                f"/personal-video/manage/"
                f"{original['token']}/entry/"
                f"{original['entry_id']}"
            ),
            data={
                "dancer_name": "",
                "dance_name": "Changed",
                "category": "Senior",
                "event_number": "",
                "performance_date": "2026-08-20",
                "performance_time": "",
            },
        )

        assert response.status_code == 422

        factory = _factory(client)

        with factory() as session:
            entry = session.get(
                PersonalVideoEntry,
                original["entry_id"],
            )

            assert entry is not None
            assert entry.dancer_name == "Performer"
            assert entry.dance_name == "Dance"
