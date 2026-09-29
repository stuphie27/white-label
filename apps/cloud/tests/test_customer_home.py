from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.db.models import (
    CloudOrder,
    CustomerDelivery,
    CustomerFavouriteSession,
    Event,
    Gallery,
)
from app.main import create_app


def _csrf(html: str) -> str:
    import re

    match = re.search(
        r'name="csrf_token"\s+value="([^"]+)"',
        html,
    )

    assert match is not None
    return match.group(1)


def _verify_customer(
    client,
    app,
    email="customer@example.com",
):
    sent = {}

    def sender(settings, address, code):
        sent["email"] = address
        sent["code"] = code

    app.state.customer_code_sender = sender

    registration = client.get(
        "/customer/register",
        params={
            "next": "/customer/home",
        },
    )

    assert registration.status_code == 200

    response = client.post(
        "/customer/register",
        data={
            "first_name": "Stuart",
            "last_name": "Customer",
            "email": email,
            "next_url": "/customer/home",
            "csrf_token": _csrf(
                registration.text
            ),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert sent["email"] == email
    assert len(sent["code"]) == 6

    verification = client.get(
        "/customer/verify"
    )

    assert verification.status_code == 200

    response = client.post(
        "/customer/verify",
        data={
            "code": sent["code"],
            "csrf_token": _csrf(
                verification.text
            ),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/customer/consent"
    )

    consent = client.get(
        "/customer/consent"
    )

    assert consent.status_code == 200

    response = client.post(
        "/customer/consent",
        data={
            "accept_terms": "yes",
            "accept_privacy": "yes",
            "accept_copyright": "yes",
            "csrf_token": _csrf(
                consent.text
            ),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/customer/home"
    )

def test_customer_home_requires_verified_customer():
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/customer/home",
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith(
            "/customer/register"
        )


def test_verified_customer_home_lists_owned_history():
    app = create_app()

    with TestClient(app) as client:
        _verify_customer(client, app)

        with app.state.session_factory() as db:
            event = Event(
                name="R7B Customer Event",
                start_date=date.today(),
                end_date=date.today(),
                venue="STUPHIE V2 LAB",
                status="live",
            )
            db.add(event)
            db.flush()

            gallery = Gallery(
                event_id=event.id,
                name="R7B Gallery",
                slug="r7b-customer-event",
                status="published",
                visibility="public",
            )
            db.add(gallery)
            db.flush()

            favourite_session = CustomerFavouriteSession(
                gallery_id=gallery.id,
                token="r7b-customer-favourite-token",
                customer_name="Stuart Customer",
                email="customer@example.com",
            )
            db.add(favourite_session)

            order = CloudOrder(
                event_id=event.id,
                customer_name="Stuart Customer",
                customer_email="customer@example.com",
                product_type="digital",
                payment_method="sumup_online",
                fulfilment_method="digital",
                order_reference="R7B-ORDER-001",
                payment_status="paid",
                payment_amount_pence=2200,
                status="complete",
            )
            db.add(order)

            delivery = CustomerDelivery(
                event_name=event.name,
                order_reference="R7B-ORDER-001",
                customer_name="Stuart Customer",
                customer_email="customer@example.com",
                delivery_type="high_res",
                token_hash="r7b-delivery-token-hash",
                zip_path="synthetic/r7b.zip",
                item_count=1,
                status="ready",
                expires_at=datetime(
                    2099,
                    1,
                    1,
                    tzinfo=timezone.utc,
                ),
                reminder_due_at=datetime(
                    2098,
                    12,
                    30,
                    tzinfo=timezone.utc,
                ),
            )
            db.add(delivery)
            db.commit()

        response = client.get("/customer/home")

        assert response.status_code == 200
        assert "Hi Stuart" in response.text
        assert "R7B Customer Event" in response.text
        assert "R7B-ORDER-001" in response.text
        assert "Digital photographs" in response.text
        assert "customer@example.com" in response.text


def test_customer_logout_forgets_verified_identity():
    app = create_app()

    with TestClient(app) as client:
        _verify_customer(client, app)

        assert client.get("/customer/home").status_code == 200

        response = client.post(
            "/customer/logout",
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/"

        response = client.get(
            "/customer/home",
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith(
            "/customer/register"
        )
