import re
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.db.models import (
    CustomerFavourite,
    CustomerFavouriteSession,
    CustomerIdentity,
    Event,
    Gallery,
    GalleryAsset,
)


def csrf_from(text: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="([^"]+)"',
        text,
    )
    assert match is not None
    return match.group(1)


def seed_gallery(app):
    with app.state.session_factory() as db:
        event = Event(
            name="R7A5 Test Event",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
            venue="R7A5 Test Venue",
            status="live",
        )
        db.add(event)
        db.commit()
        db.refresh(event)

        gallery = Gallery(
            event_id=event.id,
            name="R7A5 Test Gallery",
            slug="r7a5-test-gallery",
            status="published",
            visibility="public",
        )
        db.add(gallery)
        db.commit()
        db.refresh(gallery)

        asset = GalleryAsset(
            gallery_id=gallery.id,
            source_ref="r7a5-photo-1",
            filename="R7A5-0001.JPG",
            media_kind="photos",
            content_type="image/jpeg",
            storage_path="",
            status="ready",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)

        return str(gallery.id), str(asset.id)


def test_complete_verified_customer_cross_device_journey():
    sent_codes = []

    def fake_sender(settings, email, code):
        sent_codes.append(
            {
                "email": email,
                "code": code,
            }
        )

    app = create_app()
    app.state.customer_code_sender = fake_sender

    with TestClient(app) as phone_one:
        gallery_id, asset_id = seed_gallery(app)

        register = phone_one.get(
            "/customer/register",
            params={
                "next": "/g/r7a5-test-gallery?media=photos",
            },
        )

        assert register.status_code == 200

        register_csrf = csrf_from(register.text)

        response = phone_one.post(
            "/customer/register",
            data={
                "first_name": "Sophie",
                "last_name": "Customer",
                "email": "customer@example.com",
                "next_url": "/g/r7a5-test-gallery?media=photos",
                "csrf_token": register_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert len(sent_codes) == 1
        assert (
            sent_codes[0]["email"]
            == "customer@example.com"
        )

        verify = phone_one.get("/customer/verify")
        assert verify.status_code == 200

        verify_csrf = csrf_from(verify.text)

        response = phone_one.post(
            "/customer/verify",
            data={
                "code": sent_codes[0]["code"],
                "csrf_token": verify_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert (
            response.headers["location"]
            == "/customer/consent"
        )

        consent = phone_one.get("/customer/consent")
        assert consent.status_code == 200

        consent_csrf = csrf_from(consent.text)

        response = phone_one.post(
            "/customer/consent",
            data={
                "accept_terms": "yes",
                "accept_privacy": "yes",
                "accept_copyright": "yes",
                "csrf_token": consent_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == (
            "/g/r7a5-test-gallery?media=photos"
        )

        gallery = phone_one.get(
            "/g/r7a5-test-gallery?media=photos"
        )

        assert gallery.status_code == 200

        response = phone_one.post(
            f"/g/r7a5-test-gallery/favourites/{asset_id}",
            headers={
                "X-Requested-With": "PirouetteFavourite",
            },
        )

        assert response.status_code == 200
        payload = response.json()

        assert payload["ok"] is True
        assert payload["active"] is True
        assert payload["count"] == 1

        with app.state.session_factory() as db:
            identity = db.scalar(
                select(CustomerIdentity).where(
                    CustomerIdentity.email
                    == "customer@example.com"
                )
            )

            assert identity is not None

            favourite_session = db.scalar(
                select(CustomerFavouriteSession).where(
                    CustomerFavouriteSession.gallery_id
                    == gallery_id,
                    CustomerFavouriteSession.email
                    == "customer@example.com",
                )
            )

            assert favourite_session is not None

            favourites = list(
                db.scalars(
                    select(CustomerFavourite).where(
                        CustomerFavourite.session_id
                        == favourite_session.id
                    )
                )
            )

            assert len(favourites) == 1
            assert str(favourites[0].asset_id) == asset_id

        sent_codes.clear()

        phone_one.cookies.clear()
        phone_two = phone_one
        register = phone_two.get(
            "/customer/register",
            params={
                "next": "/g/r7a5-test-gallery/favourites",
            },
        )

        register_csrf = csrf_from(register.text)

        response = phone_two.post(
            "/customer/register",
            data={
                "first_name": "Sophie",
                "last_name": "Customer",
                "email": "customer@example.com",
                "next_url": "/g/r7a5-test-gallery/favourites",
                "csrf_token": register_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert len(sent_codes) == 1

        verify = phone_two.get("/customer/verify")
        verify_csrf = csrf_from(verify.text)

        response = phone_two.post(
            "/customer/verify",
            data={
                "code": sent_codes[0]["code"],
                "csrf_token": verify_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert (
            response.headers["location"]
            == "/customer/consent"
        )

        consent = phone_two.get("/customer/consent")
        consent_csrf = csrf_from(consent.text)

        response = phone_two.post(
            "/customer/consent",
            data={
                "accept_terms": "yes",
                "accept_privacy": "yes",
                "accept_copyright": "yes",
                "csrf_token": consent_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        favourites = phone_two.get(
            "/g/r7a5-test-gallery/favourites"
        )

        assert favourites.status_code == 200
        assert "R7A5-0001.JPG" in favourites.text
