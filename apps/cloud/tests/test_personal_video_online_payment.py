from __future__ import annotations

import importlib
import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    Event,
    PersonalVideoBooking,
    PersonalVideoEntry,
)
from app.main import create_app
from app.payments.sumup import SumUpError


pv_router = importlib.import_module(
    "app.personal_video.router"
)


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
                source_ref=(
                    f"pv-14f2-{booking_type}"
                ),
                name=(
                    f"14F2 {booking_type.title()} Event"
                ),
                start_date=date(2026, 8, 20),
                end_date=date(2026, 8, 21),
                venue="14F2 Test Venue",
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


def _latest_booking(client):
    with _factory(client)() as session:
        return session.scalar(
            select(PersonalVideoBooking)
            .order_by(
                PersonalVideoBooking.created_at.desc()
            )
            .limit(1)
        )


def _seed_payment_booking(
    client,
    *,
    booking_type: str,
    initial_payment_pence: int,
    final_total_pence: int,
):
    _live_event(client, booking_type)

    factory = _factory(client)

    with factory() as session:
        event = session.scalar(
            select(Event).limit(1)
        )

        booking = PersonalVideoBooking(
            event_id=event.id,
            reference=(
                f"PV-14F2-{booking_type}"
            ),
            manage_token=(
                f"manage-14f2-{booking_type}"
            ),
            booking_type=booking_type,
            customer_name="Payment Customer",
            customer_email="pay@example.com",
            customer_phone="07123456789",
            status=(
                "initial_payment_due"
                if booking_type == "ballroom"
                else "payment_due"
            ),
            payment_status="awaiting_payment",
            initial_payment_pence=(
                initial_payment_pence
            ),
            total_paid_pence=0,
            final_total_pence=(
                final_total_pence
            ),
            payment_method="sumup_online",
            sumup_checkout_id=(
                f"checkout-{booking_type}"
            ),
        )

        session.add(booking)
        session.flush()

        session.add(
            PersonalVideoEntry(
                booking_id=booking.id,
                dancer_name="Dancer",
                performance_date=date(
                    2026,
                    8,
                    20,
                ),
                expected_video_count=1,
                unit_price_pence=2300,
                status="awaiting_payment",
            )
        )

        session.commit()

        return {
            "id": booking.id,
            "token": booking.manage_token,
            "checkout_id": (
                booking.sumup_checkout_id
            ),
        }


def test_payment_amount_contract():
    ballroom = PersonalVideoBooking(
        booking_type="ballroom",
        initial_payment_pence=3000,
        final_total_pence=6000,
    )

    freestyle = PersonalVideoBooking(
        booking_type="freestyle",
        initial_payment_pence=0,
        final_total_pence=4600,
    )

    theatre = PersonalVideoBooking(
        booking_type="theatre",
        initial_payment_pence=0,
        final_total_pence=6900,
    )

    assert (
        pv_router._personal_video_payment_amount(
            ballroom
        )
        == 3000
    )

    assert (
        pv_router._personal_video_payment_amount(
            freestyle
        )
        == 4600
    )

    assert (
        pv_router._personal_video_payment_amount(
            theatre
        )
        == 6900
    )


def test_freestyle_online_booking_redirects_to_sumup(
    monkeypatch,
):
    captured = {}

    def fake_checkout(
        settings,
        *,
        reference,
        amount_pence,
        description,
        redirect_url,
        return_url,
    ):
        captured.update(
            {
                "reference": reference,
                "amount_pence": amount_pence,
                "redirect_url": redirect_url,
                "return_url": return_url,
            }
        )

        return SimpleNamespace(
            checkout_id="sumup-test-123",
            checkout_url=(
                "https://pay.example/"
                "sumup-test-123"
            ),
            status="PENDING",
        )

    monkeypatch.setattr(
        pv_router,
        "create_hosted_checkout",
        fake_checkout,
    )

    with TestClient(create_app()) as client:
        _live_event(client, "freestyle")

        response = client.post(
            "/personal-video/freestyle",
            data={
                "customer_name": "Parent",
                "customer_email": (
                    "parent@example.com"
                ),
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
                            "performance_date": (
                                "2026-08-20"
                            ),
                            "performance_time": "",
                        }
                    ]
                ),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == (
            "https://pay.example/"
            "sumup-test-123"
        )

        booking = _latest_booking(client)

        assert booking is not None
        assert (
            booking.sumup_checkout_id
            == "sumup-test-123"
        )
        assert (
            booking.payment_status
            == "awaiting_payment"
        )
        assert booking.total_paid_pence == 0
        assert captured["amount_pence"] == 2300
        assert "/personal-video/payment/" in (
            captured["redirect_url"]
        )
        assert (
            "/api/payments/personal-video/"
            in captured["return_url"]
        )


def test_checkout_failure_preserves_booking(
    monkeypatch,
):
    def fail_checkout(*args, **kwargs):
        raise SumUpError(
            "Simulated SumUp failure"
        )

    monkeypatch.setattr(
        pv_router,
        "create_hosted_checkout",
        fail_checkout,
    )

    with TestClient(create_app()) as client:
        _live_event(client, "freestyle")

        response = client.post(
            "/personal-video/freestyle",
            data={
                "customer_name": "Parent",
                "customer_email": (
                    "parent@example.com"
                ),
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
                            "performance_date": (
                                "2026-08-20"
                            ),
                            "performance_time": "",
                        }
                    ]
                ),
            },
        )

        assert response.status_code == 200
        assert (
            "No payment has been taken"
            in response.text
        )

        booking = _latest_booking(client)

        assert booking is not None
        assert booking.sumup_checkout_id is None
        assert booking.total_paid_pence == 0
        assert (
            booking.payment_status
            == "awaiting_payment"
        )


def test_verified_freestyle_payment_marks_full_booking_paid(
    monkeypatch,
):
    with TestClient(create_app()) as client:
        seeded = _seed_payment_booking(
            client,
            booking_type="freestyle",
            initial_payment_pence=0,
            final_total_pence=4600,
        )

        monkeypatch.setattr(
            pv_router,
            "retrieve_checkout",
            lambda settings, checkout_id: {
                "id": checkout_id,
                "status": "PAID",
                "currency": (
                    settings.sumup_currency
                ),
                "amount": "46.00",
            },
        )

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            status = (
                pv_router
                ._apply_personal_video_checkout(
                    session,
                    client.app.state.settings,
                    booking,
                )
            )

            assert status == "PAID"

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            entry = session.scalar(
                select(PersonalVideoEntry)
                .where(
                    PersonalVideoEntry.booking_id
                    == booking.id
                )
                .limit(1)
            )

            assert booking.payment_status == "paid"
            assert booking.status == "booked"
            assert booking.total_paid_pence == 4600
            assert booking.paid_at is not None
            assert entry.status == "booked"


def test_verified_ballroom_initial_payment_is_part_paid(
    monkeypatch,
):
    with TestClient(create_app()) as client:
        seeded = _seed_payment_booking(
            client,
            booking_type="ballroom",
            initial_payment_pence=3000,
            final_total_pence=6000,
        )

        monkeypatch.setattr(
            pv_router,
            "retrieve_checkout",
            lambda settings, checkout_id: {
                "id": checkout_id,
                "status": "PAID",
                "currency": (
                    settings.sumup_currency
                ),
                "amount": "30.00",
            },
        )

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            pv_router._apply_personal_video_checkout(
                session,
                client.app.state.settings,
                booking,
            )

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            assert (
                booking.payment_status
                == "part_paid"
            )
            assert booking.status == "booked"
            assert booking.total_paid_pence == 3000


@pytest.mark.parametrize(
    ("amount", "currency"),
    [
        ("45.99", "GBP"),
        ("46.00", "USD"),
    ],
)
def test_mismatched_checkout_never_marks_booking_paid(
    monkeypatch,
    amount,
    currency,
):
    with TestClient(create_app()) as client:
        seeded = _seed_payment_booking(
            client,
            booking_type="freestyle",
            initial_payment_pence=0,
            final_total_pence=4600,
        )

        monkeypatch.setattr(
            pv_router,
            "retrieve_checkout",
            lambda settings, checkout_id: {
                "id": checkout_id,
                "status": "PAID",
                "currency": currency,
                "amount": amount,
            },
        )

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            with pytest.raises(SumUpError):
                pv_router._apply_personal_video_checkout(
                    session,
                    client.app.state.settings,
                    booking,
                )

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            assert (
                booking.payment_status
                == "awaiting_payment"
            )
            assert booking.total_paid_pence == 0


def test_paid_verification_is_idempotent(
    monkeypatch,
):
    calls = {"count": 0}

    def paid_checkout(settings, checkout_id):
        calls["count"] += 1

        return {
            "id": checkout_id,
            "status": "PAID",
            "currency": settings.sumup_currency,
            "amount": "23.00",
        }

    monkeypatch.setattr(
        pv_router,
        "retrieve_checkout",
        paid_checkout,
    )

    with TestClient(create_app()) as client:
        seeded = _seed_payment_booking(
            client,
            booking_type="theatre",
            initial_payment_pence=0,
            final_total_pence=2300,
        )

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            assert (
                pv_router
                ._apply_personal_video_checkout(
                    session,
                    client.app.state.settings,
                    booking,
                )
                == "PAID"
            )

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            assert (
                pv_router
                ._apply_personal_video_checkout(
                    session,
                    client.app.state.settings,
                    booking,
                )
                == "PAID"
            )

        assert calls["count"] == 1

        with _factory(client)() as session:
            booking = session.get(
                PersonalVideoBooking,
                seeded["id"],
            )

            assert booking.total_paid_pence == 2300


def test_foreign_webhook_checkout_is_ignored(
    monkeypatch,
):
    called = {"value": False}

    def should_not_retrieve(*args, **kwargs):
        called["value"] = True
        raise AssertionError(
            "Foreign checkout must not be verified"
        )

    monkeypatch.setattr(
        pv_router,
        "retrieve_checkout",
        should_not_retrieve,
    )

    with TestClient(create_app()) as client:
        seeded = _seed_payment_booking(
            client,
            booking_type="theatre",
            initial_payment_pence=0,
            final_total_pence=2300,
        )

        response = client.post(
            (
                "/api/payments/personal-video/"
                f"{seeded['token']}/sumup"
            ),
            json={
                "id": "different-checkout",
                "event_type": (
                    "CHECKOUT_STATUS_CHANGED"
                ),
            },
        )

        assert response.status_code == 204
        assert called["value"] is False
