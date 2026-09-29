from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.customer_ownership import verified_customer
from app.db.models import (
    CloudOrder,
    CustomerDelivery,
    CustomerFavouriteSession,
    Event,
    Gallery,
)


def build_customer_home_router(
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter(tags=["customer-home"])

    @router.get(
        "/customer/home",
        include_in_schema=False,
    )
    async def customer_home(request: Request):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return RedirectResponse(
                "/customer/register?next=/customer/home",
                status_code=303,
            )

        with session_factory() as db:
            customer = verified_customer(db, request)

            if customer is None:
                return RedirectResponse(
                    "/customer/register?next=/customer/home",
                    status_code=303,
                )

            email = str(customer.email or "").strip().lower()

            galleries = list(
                db.scalars(
                    select(Gallery)
                    .join(Event, Gallery.event_id == Event.id)
                    .where(
                        Gallery.status == "published",
                    )
                    .order_by(
                        Event.start_date.desc(),
                        Gallery.updated_at.desc(),
                    )
                ).all()
            )

            event_by_id = {
                str(event.id): event
                for event in db.scalars(
                    select(Event).order_by(
                        Event.start_date.desc(),
                        Event.updated_at.desc(),
                    )
                ).all()
            }

            favourite_sessions = list(
                db.scalars(
                    select(CustomerFavouriteSession)
                    .where(
                        CustomerFavouriteSession.email == email,
                    )
                    .order_by(
                        CustomerFavouriteSession.updated_at.desc(),
                    )
                ).all()
            )

            favourite_by_gallery = {
                str(item.gallery_id): item
                for item in favourite_sessions
            }

            customer_events = []

            for gallery in galleries:
                favourite_session = favourite_by_gallery.get(
                    str(gallery.id)
                )

                if favourite_session is None:
                    continue

                event = event_by_id.get(str(gallery.event_id))

                if event is None:
                    continue

                customer_events.append(
                    {
                        "event": event,
                        "gallery": gallery,
                        "favourite_session": favourite_session,
                    }
                )

            current_event = None

            for item in customer_events:
                event = item["event"]
                gallery = item["gallery"]

                if (
                    event.status == "live"
                    and (
                        not gallery.expires_at
                        or gallery.expires_at >= date.today()
                    )
                ):
                    current_event = item
                    break

            orders = list(
                db.scalars(
                    select(CloudOrder)
                    .where(
                        CloudOrder.customer_email == email,
                    )
                    .order_by(
                        CloudOrder.created_at.desc(),
                    )
                ).all()
            )

            deliveries = list(
                db.scalars(
                    select(CustomerDelivery)
                    .where(
                        CustomerDelivery.customer_email == email,
                    )
                    .order_by(
                        CustomerDelivery.created_at.desc(),
                    )
                ).all()
            )

            first_name = str(
                customer.first_name or ""
            ).strip()

            return templates.TemplateResponse(
                request=request,
                name="customer_home.html",
                context={
                    "customer": customer,
                    "first_name": first_name,
                    "current_event": current_event,
                    "customer_events": customer_events,
                    "orders": orders,
                    "deliveries": deliveries,
                },
            )

    @router.post(
        "/customer/logout",
        include_in_schema=False,
    )
    async def customer_logout(request: Request):
        request.session.pop(
            "verified_customer_id",
            None,
        )
        request.session.pop(
            "verified_customer_at",
            None,
        )
        request.session.pop(
            "pending_verified_customer_id",
            None,
        )

        return RedirectResponse(
            "/",
            status_code=303,
        )

    return router
