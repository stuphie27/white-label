from __future__ import annotations

import json
import secrets
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.payments.sumup import (
    SumUpError,
    create_hosted_checkout,
    retrieve_checkout,
)
from app.db.models import (
    Event,
    PersonalVideoBooking,
    PersonalVideoEntry,
)


PERSONAL_VIDEO_TYPES = {
    "ballroom": "Ballroom",
    "freestyle": "Freestyle",
    "theatre": "Theatre",
}

FREESTYLE_STYLES = (
    "Slow",
    "Fast",
    "Pairs",
    "Trios",
    "Team",
)

FREESTYLE_CATEGORIES = (
    "Beginner",
    "Starter",
    "Intermediate",
    "Champ",
    "Prem",
)

FREESTYLE_AGE_GROUPS = (
    "U4",
    "U6",
    "U8",
    "U10",
    "U12",
    "U14",
    "U16",
    "U18",
    "O18",
)

TERMS_VERSION = "2026-08-18"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value, limit: int | None = None) -> str:
    text = str(value or "").strip()
    if limit is not None:
        text = text[:limit]
    return text


def _valid_email(value: str) -> bool:
    value = value.strip()
    return bool(
        value
        and "@" in value
        and not value.startswith("@")
        and not value.endswith("@")
    )


def _parse_date(value: str) -> date | None:
    value = _clean(value)

    if not value:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_time(value: str) -> time | None:
    value = _clean(value)

    if not value:
        return None

    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def _reference() -> str:
    return "SPV-" + secrets.token_hex(4).upper()


def _live_event(session, booking_type: str | None = None):
    conditions = [
        Event.status == "live",
        Event.personal_video_enabled.is_(True),
    ]

    if booking_type is not None:
        conditions.append(
            Event.personal_video_type == booking_type
        )

    return session.scalar(
        select(Event)
        .where(*conditions)
        .order_by(
            Event.mirrored_last_sync_at.desc(),
            Event.updated_at.desc(),
        )
        .limit(1)
    )


def _not_found(
    templates: Jinja2Templates,
    request: Request,
):
    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context={},
        status_code=404,
    )


def _form_context(
    event,
    booking_type: str,
    *,
    form_error: str = "",
    form_data: dict | None = None,
) -> dict:
    context = {
        "event": event,
        "form_error": form_error,
        "form_data": form_data or {},
    }

    if booking_type == "freestyle":
        context.update(
            {
                "styles": FREESTYLE_STYLES,
                "categories": FREESTYLE_CATEGORIES,
                "age_groups": FREESTYLE_AGE_GROUPS,
                "unit_price_pence": 2300,
            }
        )

    return context


def _form_response(
    templates: Jinja2Templates,
    request: Request,
    event,
    booking_type: str,
    *,
    form_error: str = "",
    form_data: dict | None = None,
    status_code: int = 200,
):
    response = templates.TemplateResponse(
        request=request,
        name=f"personal_video_{booking_type}.html",
        context=_form_context(
            event,
            booking_type,
            form_error=form_error,
            form_data=form_data,
        ),
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _saved_response(
    templates: Jinja2Templates,
    request: Request,
    event,
    booking: PersonalVideoBooking,
):
    response = templates.TemplateResponse(
        request=request,
        name="personal_video_booking_saved.html",
        context={
            "event": event,
            "booking": booking,
            "booking_type_label": PERSONAL_VIDEO_TYPES[
                booking.booking_type
            ],
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _parse_entries(form) -> list[dict]:
    try:
        entries = json.loads(
            _clean(form.get("entries_json")) or "[]"
        )
    except (TypeError, json.JSONDecodeError):
        return []

    if not isinstance(entries, list):
        return []

    return [
        entry
        for entry in entries
        if isinstance(entry, dict)
    ]


def _common_form_data(form) -> dict:
    return {
        "customer_name": _clean(
            form.get("customer_name"),
            200,
        ),
        "customer_email": _clean(
            form.get("customer_email"),
            320,
        ),
        "customer_phone": _clean(
            form.get("customer_phone"),
            100,
        ),
        "customer_notes": _clean(
            form.get("customer_notes")
        ),
        "video_format": _clean(
            form.get("video_format"),
            80,
        ),
        "terms_accepted": (
            _clean(form.get("terms_accepted")).lower()
            in {"yes", "on", "true", "1"}
        ),
    }


def _common_error(data: dict) -> str:
    if not data["customer_name"]:
        return "Dancer/s names are required."

    if not _valid_email(data["customer_email"]):
        return "A valid email address is required."

    if not data["customer_phone"]:
        return "Telephone number is required."

    if data["video_format"] not in {
        "Vertical",
        "Horizontal",
    }:
        return "Please select a video format."

    if not data["terms_accepted"]:
        return (
            "Please accept the Personal Video Recording Policy."
        )

    return ""


def _new_booking(
    *,
    event,
    booking_type: str,
    data: dict,
    initial_payment_pence: int,
    final_total_pence: int,
    minimum_video_count: int = 0,
    competitor_number: str = "",
    outfit_description: str = "",
    intended_use: str = "",
) -> PersonalVideoBooking:
    return PersonalVideoBooking(
        event_id=event.id,
        reference=_reference(),
        manage_token=secrets.token_urlsafe(32),
        booking_type=booking_type,
        customer_name=data["customer_name"],
        customer_email=data["customer_email"],
        customer_phone=data["customer_phone"],
        status="new",
        payment_status="awaiting_payment",
        initial_payment_pence=initial_payment_pence,
        total_paid_pence=0,
        final_total_pence=final_total_pence,
        customer_notes=data["customer_notes"],
        competitor_number=competitor_number,
        outfit_description=outfit_description,
        identification_photo_path="",
        minimum_video_count=minimum_video_count,
        video_format=data["video_format"],
        intended_use=intended_use,
        terms_accepted_at=_now(),
        terms_version=TERMS_VERSION,
    )



def _personal_video_payment_amount(
    booking: PersonalVideoBooking,
) -> int:
    if booking.booking_type == "ballroom":
        return max(
            0,
            int(booking.initial_payment_pence or 0),
        )

    return max(
        0,
        int(booking.final_total_pence or 0),
    )


def _personal_video_due_status(
    booking: PersonalVideoBooking,
) -> str:
    if booking.booking_type == "ballroom":
        return "initial_payment_due"

    return "payment_due"


def _personal_video_payment_description(
    booking: PersonalVideoBooking,
) -> str:
    if booking.booking_type == "ballroom":
        return (
            "Sophie’s Photography Ballroom "
            "Personal Video Initial Payment "
            f"{booking.reference}"
        )

    return (
        "Sophie’s Photography Personal Video "
        f"{booking.reference}"
    )


def _personal_video_saved_response(
    templates,
    request: Request,
    event: Event,
    booking: PersonalVideoBooking,
    *,
    payment_error: str = "",
    payment_result: str = "",
    stand_payment: bool = False,
):
    response = templates.TemplateResponse(
        request=request,
        name="personal_video_booking_saved.html",
        context={
            "event": event,
            "booking": booking,
            "booking_type_label": (
                PERSONAL_VIDEO_TYPES.get(
                    booking.booking_type,
                    "Personal Video",
                )
            ),
            "payment_error": payment_error,
            "payment_result": payment_result,
            "stand_payment": stand_payment,
        },
    )

    response.headers["Cache-Control"] = (
        "no-store, no-cache, "
        "must-revalidate, private"
    )

    return response


def _create_personal_video_checkout(
    session,
    request: Request,
    booking: PersonalVideoBooking,
):
    amount_pence = _personal_video_payment_amount(
        booking
    )

    if amount_pence <= 0:
        raise SumUpError(
            "Personal Video payment amount "
            "must be greater than zero"
        )

    settings = request.app.state.settings
    base = str(
        settings.public_base_url or ""
    ).rstrip("/")

    if not base:
        raise SumUpError(
            "Public Cloud URL is not configured"
        )

    payment_return = (
        f"{base}/personal-video/payment/"
        f"{booking.manage_token}"
    )

    webhook_return = (
        f"{base}/api/payments/personal-video/"
        f"{booking.manage_token}/sumup"
    )

    checkout = create_hosted_checkout(
        settings,
        reference=(
            f"personal-video-{booking.reference}"
        ),
        amount_pence=amount_pence,
        description=(
            _personal_video_payment_description(
                booking
            )
        ),
        redirect_url=payment_return,
        return_url=webhook_return,
    )

    booking.sumup_checkout_id = (
        checkout.checkout_id
    )
    booking.payment_method = "sumup_online"
    booking.payment_status = "awaiting_payment"
    booking.status = _personal_video_due_status(
        booking
    )

    session.commit()
    session.refresh(booking)

    return checkout


def _apply_personal_video_checkout(
    session,
    settings,
    booking: PersonalVideoBooking,
) -> str:
    if (
        booking.booking_type == "ballroom"
        and booking.payment_status
        in {"part_paid", "paid"}
    ):
        return "PAID"

    if (
        booking.booking_type != "ballroom"
        and booking.payment_status == "paid"
    ):
        return "PAID"

    checkout_id = str(
        booking.sumup_checkout_id or ""
    ).strip()

    if not checkout_id:
        raise SumUpError(
            "No SumUp checkout is attached "
            "to this Personal Video booking"
        )

    result = retrieve_checkout(
        settings,
        checkout_id,
    )

    status = str(
        result.get("status") or ""
    ).upper()

    actual_currency = str(
        result.get("currency") or ""
    ).upper()

    expected_currency = str(
        settings.sumup_currency or ""
    ).upper()

    expected_pence = (
        _personal_video_payment_amount(
            booking
        )
    )

    try:
        actual_pence = int(
            (
                Decimal(str(result.get("amount")))
                * Decimal(100)
            ).quantize(Decimal("1"))
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise SumUpError(
            "SumUp returned an invalid "
            "Personal Video checkout amount"
        ) from exc

    if (
        actual_currency != expected_currency
        or actual_pence != expected_pence
    ):
        raise SumUpError(
            "SumUp checkout amount or currency "
            "did not match the Personal Video booking"
        )

    if status == "PAID":
        if booking.booking_type == "ballroom":
            paid_pence = max(
                int(
                    booking.total_paid_pence
                    or 0
                ),
                int(
                    booking.initial_payment_pence
                    or 0
                ),
            )

            booking.total_paid_pence = (
                paid_pence
            )

            if (
                paid_pence
                >= int(
                    booking.final_total_pence
                    or 0
                )
            ):
                booking.payment_status = "paid"
            else:
                booking.payment_status = (
                    "part_paid"
                )
        else:
            booking.total_paid_pence = int(
                booking.final_total_pence or 0
            )
            booking.payment_status = "paid"

        booking.payment_method = "sumup_online"
        booking.status = "booked"
        booking.paid_at = _now()

        entries = list(
            session.scalars(
                select(PersonalVideoEntry)
                .where(
                    PersonalVideoEntry.booking_id
                    == booking.id
                )
            )
        )

        for entry in entries:
            entry.status = "booked"

    elif status == "FAILED":
        booking.payment_status = "failed"
        booking.status = (
            _personal_video_due_status(
                booking
            )
        )

    elif status == "EXPIRED":
        booking.payment_status = "expired"
        booking.status = (
            _personal_video_due_status(
                booking
            )
        )

    else:
        booking.payment_status = (
            "awaiting_payment"
        )
        booking.status = (
            _personal_video_due_status(
                booking
            )
        )

    session.commit()
    session.refresh(booking)

    return status


def _complete_personal_video_submission(
    templates,
    session,
    request: Request,
    event: Event,
    booking: PersonalVideoBooking,
    payment_choice: str,
):
    payment_choice = str(
        payment_choice or ""
    ).strip().lower()

    booking.status = (
        _personal_video_due_status(
            booking
        )
    )

    if payment_choice == "stand":
        booking.payment_method = "service_desk"
        booking.payment_status = "awaiting_payment"

        session.commit()
        session.refresh(booking)

        return _personal_video_saved_response(
            templates,
            request,
            event,
            booking,
            stand_payment=True,
        )

    if payment_choice != "online":
        raise ValueError(
            "Unsupported Personal Video payment choice"
        )

    booking.payment_method = "sumup_online"
    booking.payment_status = "awaiting_payment"

    # Commit the customer booking before any external
    # payment API call. SumUp failure must never erase
    # the booking.
    session.commit()
    session.refresh(booking)

    try:
        checkout = (
            _create_personal_video_checkout(
                session,
                request,
                booking,
            )
        )

        return RedirectResponse(
            url=checkout.checkout_url,
            status_code=303,
        )

    except SumUpError:
        # Booking remains safely persisted and unpaid.
        booking.payment_status = (
            "awaiting_payment"
        )
        booking.status = (
            _personal_video_due_status(
                booking
            )
        )

        session.commit()
        session.refresh(booking)

        return _personal_video_saved_response(
            templates,
            request,
            event,
            booking,
            payment_error=(
                "Secure online payment is temporarily "
                "unavailable. Your booking has been "
                "saved. No payment has been taken."
            ),
        )


def build_personal_video_router(
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/personal-video",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_entry(request: Request):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(templates, request)

        with session_factory() as session:
            event = _live_event(session)

            if (
                event is None
                or event.personal_video_type
                not in PERSONAL_VIDEO_TYPES
            ):
                return _not_found(templates, request)

            response = templates.TemplateResponse(
                request=request,
                name="personal_video_online.html",
                context={
                    "event": event,
                    "booking_type": event.personal_video_type,
                    "booking_type_label": PERSONAL_VIDEO_TYPES[
                        event.personal_video_type
                    ],
                },
            )
            response.headers["Cache-Control"] = "no-store"
            return response

    def render_personal_video_form(
        request: Request,
        booking_type: str,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(templates, request)

        with session_factory() as session:
            event = _live_event(
                session,
                booking_type,
            )

            if event is None:
                return _not_found(templates, request)

            return _form_response(
                templates,
                request,
                event,
                booking_type,
            )

    @router.get(
        "/personal-video/ballroom",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_ballroom(
        request: Request,
    ):
        return render_personal_video_form(
            request,
            "ballroom",
        )

    @router.post(
        "/personal-video/ballroom",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_ballroom_submit(
        request: Request,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(templates, request)

        form = await request.form()

        payment_choice = _clean(
            form.get("payment_choice") or "online",
            40,
        ).lower()

        data = _common_form_data(form)

        partner_email = _clean(
            form.get("partner_email"),
            320,
        )
        competitor_number = _clean(
            form.get("competitor_number"),
            100,
        )
        outfit_description = _clean(
            form.get("outfit_description")
        )

        try:
            minimum_video_count = max(
                1,
                int(
                    form.get("minimum_video_count")
                    or 1
                ),
            )
        except (TypeError, ValueError):
            minimum_video_count = 1

        entries = _parse_entries(form)

        intended_use = json.dumps(
            [
                _clean(value)
                for value in form.getlist("intended_use")
                if _clean(value)
            ],
            ensure_ascii=False,
        )

        form_data = {
            **data,
            "partner_email": partner_email,
            "competitor_number": competitor_number,
            "outfit_description": outfit_description,
            "minimum_video_count": minimum_video_count,
            "entries_json": _clean(
                form.get("entries_json")
            ),
        }

        error = _common_error(data)

        if not error and partner_email:
            if not _valid_email(partner_email):
                error = (
                    "A valid partner email address is required."
                )

        if not error and not entries:
            error = (
                "Please add at least one competition event."
            )

        if not error:
            for entry in entries:
                performance_date = _clean(
                    entry.get("performance_date")
                )
                performance_time = _clean(
                    entry.get("performance_time")
                )

                if not performance_date:
                    error = "Event date is required."
                    break

                if _parse_date(performance_date) is None:
                    error = "Please enter a valid event date."
                    break

                if (
                    performance_time
                    and _parse_time(performance_time) is None
                ):
                    error = "Please enter a valid event time."
                    break

                try:
                    expected_count = int(
                        entry.get(
                            "expected_video_count"
                        )
                        or 1
                    )
                except (TypeError, ValueError):
                    expected_count = 0

                if expected_count < 1:
                    error = (
                        "Number of dances expected "
                        "must be at least 1."
                    )
                    break

                if not _clean(
                    entry.get("filming_instruction")
                ):
                    error = (
                        "Please select how you would "
                        "like this event filmed."
                    )
                    break

        with session_factory() as session:
            event = _live_event(
                session,
                "ballroom",
            )

            if event is None:
                return _not_found(templates, request)

            if error:
                return _form_response(
                    templates,
                    request,
                    event,
                    "ballroom",
                    form_error=error,
                    form_data=form_data,
                    status_code=422,
                )

            unit_price_pence = 3000
            minimum_total = (
                minimum_video_count
                * unit_price_pence
            )
            initial_payment = (
                minimum_total + 1
            ) // 2

            booking = _new_booking(
                event=event,
                booking_type="ballroom",
                data=data,
                initial_payment_pence=initial_payment,
                final_total_pence=minimum_total,
                minimum_video_count=minimum_video_count,
                competitor_number=competitor_number,
                outfit_description=outfit_description,
                intended_use=intended_use,
            )

            session.add(booking)
            session.flush()

            for entry in entries:
                session.add(
                    PersonalVideoEntry(
                        booking_id=booking.id,
                        dancer_name=data["customer_name"],
                        dancer_number=_clean(
                            entry.get("dancer_number"),
                            100,
                        ),
                        event_number=_clean(
                            entry.get("event_number"),
                            100,
                        ),
                        event_name=_clean(
                            entry.get("event_name"),
                            240,
                        ),
                        dance_name=_clean(
                            entry.get("dance_name"),
                            240,
                        ),
                        performance_date=_parse_date(
                            _clean(
                                entry.get(
                                    "performance_date"
                                )
                            )
                        ),
                        performance_time=_parse_time(
                            _clean(
                                entry.get(
                                    "performance_time"
                                )
                            )
                        ),
                        expected_video_count=max(
                            1,
                            int(
                                entry.get(
                                    "expected_video_count"
                                )
                                or 1
                            ),
                        ),
                        filming_instruction=_clean(
                            entry.get(
                                "filming_instruction"
                            )
                        ),
                        filming_details=_clean(
                            entry.get("filming_details")
                        ),
                        unit_price_pence=unit_price_pence,
                        status="new",
                        outfit_description=_clean(
                            entry.get(
                                "outfit_description"
                            )
                        )
                        or outfit_description,
                    )
                )

            session.commit()
            session.refresh(booking)

            return _complete_personal_video_submission(
                templates,
                session,
                request,
                event,
                booking,
                payment_choice,
            )

    @router.get(
        "/personal-video/freestyle",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_freestyle(
        request: Request,
    ):
        return render_personal_video_form(
            request,
            "freestyle",
        )

    @router.post(
        "/personal-video/freestyle",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_freestyle_submit(
        request: Request,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(templates, request)

        form = await request.form()

        customer_name = _clean(
            form.get("customer_name"),
            200,
        )
        customer_email = _clean(
            form.get("customer_email"),
            320,
        )
        customer_phone = _clean(
            form.get("customer_phone"),
            100,
        )
        payment_choice = _clean(
            form.get("payment_choice") or "online",
            40,
        ).lower()

        entries = _parse_entries(form)

        error = ""

        if not customer_name:
            error = "Customer name is required."
        elif not _valid_email(customer_email):
            error = (
                "A valid customer email address is required."
            )
        elif not entries:
            error = "At least one Spot Dance is required."
        elif payment_choice not in {
            "online",
            "stand",
        }:
            error = "Please select a payment option."

        cleaned_entries = []

        if not error:
            for raw in entries:
                dancer_number = _clean(
                    raw.get("dancer_number"),
                    100,
                )
                dancer_name = _clean(
                    raw.get("dancer_name"),
                    200,
                )
                dance_style = _clean(
                    raw.get("dance_style"),
                    120,
                )
                category = _clean(
                    raw.get("category"),
                    120,
                )
                age_group = _clean(
                    raw.get("age_group"),
                    80,
                )
                performance_date = _clean(
                    raw.get("performance_date"),
                    20,
                )
                performance_time = _clean(
                    raw.get("performance_time"),
                    20,
                )

                if not dancer_number:
                    error = "Dancer number is required."
                    break

                if not dancer_name:
                    error = "Dancer name is required."
                    break

                if dance_style not in FREESTYLE_STYLES:
                    error = "Invalid Freestyle dance style."
                    break

                if category not in FREESTYLE_CATEGORIES:
                    error = "Invalid Freestyle category."
                    break

                if age_group not in FREESTYLE_AGE_GROUPS:
                    error = "Invalid Freestyle age group."
                    break

                if not performance_date:
                    error = "Dance date is required."
                    break

                if _parse_date(performance_date) is None:
                    error = "Invalid dance date."
                    break

                if (
                    performance_time
                    and _parse_time(performance_time) is None
                ):
                    error = "Invalid dance time."
                    break

                cleaned_entries.append(
                    {
                        "dancer_number": dancer_number,
                        "dancer_name": dancer_name,
                        "dance_style": dance_style,
                        "category": category,
                        "age_group": age_group,
                        "performance_date": performance_date,
                        "performance_time": performance_time,
                    }
                )

        with session_factory() as session:
            event = _live_event(
                session,
                "freestyle",
            )

            if event is None:
                return _not_found(templates, request)

            if error:
                return _form_response(
                    templates,
                    request,
                    event,
                    "freestyle",
                    form_error=error,
                    form_data={
                        "customer_name": customer_name,
                        "customer_email": customer_email,
                        "customer_phone": customer_phone,
                        "entries_json": _clean(
                            form.get("entries_json")
                        ),
                    },
                    status_code=422,
                )

            unit_price_pence = 2300
            total_pence = (
                len(cleaned_entries)
                * unit_price_pence
            )

            booking = PersonalVideoBooking(
                event_id=event.id,
                reference=_reference(),
                manage_token=secrets.token_urlsafe(32),
                booking_type="freestyle",
                customer_name=customer_name,
                customer_email=customer_email,
                customer_phone=customer_phone,
                status="payment_due",
                payment_status="awaiting_payment",
                initial_payment_pence=0,
                total_paid_pence=0,
                final_total_pence=total_pence,
                payment_method=(
                    "service_desk"
                    if payment_choice == "stand"
                    else "sumup_online"
                ),
            )

            session.add(booking)
            session.flush()

            for item in cleaned_entries:
                session.add(
                    PersonalVideoEntry(
                        booking_id=booking.id,
                        dancer_name=item["dancer_name"],
                        dancer_number=item["dancer_number"],
                        dance_style=item["dance_style"],
                        category=item["category"],
                        age_group=item["age_group"],
                        performance_date=_parse_date(
                            item["performance_date"]
                        ),
                        performance_time=_parse_time(
                            item["performance_time"]
                        ),
                        expected_video_count=1,
                        unit_price_pence=unit_price_pence,
                        status="awaiting_payment",
                    )
                )

            session.commit()
            session.refresh(booking)

            return _complete_personal_video_submission(
                templates,
                session,
                request,
                event,
                booking,
                payment_choice,
            )

    @router.get(
        "/personal-video/theatre",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_theatre(
        request: Request,
    ):
        return render_personal_video_form(
            request,
            "theatre",
        )

    @router.post(
        "/personal-video/theatre",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_theatre_submit(
        request: Request,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(templates, request)

        form = await request.form()

        customer_name = _clean(
            form.get("customer_name"),
            200,
        )
        customer_email = _clean(
            form.get("customer_email"),
            320,
        )
        customer_phone = _clean(
            form.get("customer_phone"),
            100,
        )
        payment_choice = _clean(
            form.get("payment_choice") or "online",
            40,
        ).lower()

        entries = _parse_entries(form)

        error = ""

        if not customer_name:
            error = "Customer name is required."
        elif not _valid_email(customer_email):
            error = (
                "A valid customer email address is required."
            )
        elif not customer_phone:
            error = "Telephone number is required."
        elif not entries:
            error = (
                "At least one Theatre performance is required."
            )
        elif payment_choice not in {
            "online",
            "stand",
        }:
            error = "Please select a payment option."

        cleaned_entries = []

        if not error:
            for raw in entries:
                dancer_name = _clean(
                    raw.get("dancer_name"),
                    200,
                )
                dance_name = _clean(
                    raw.get("dance_name"),
                    240,
                )
                category = _clean(
                    raw.get("category"),
                    120,
                )
                event_number = _clean(
                    raw.get("event_number"),
                    100,
                )
                performance_date = _clean(
                    raw.get("performance_date"),
                    20,
                )
                performance_time = _clean(
                    raw.get("performance_time"),
                    20,
                )

                if not dancer_name:
                    error = (
                        "Dancer or group name is required."
                    )
                    break

                if not dance_name:
                    error = "Exact dance name is required."
                    break

                if not category:
                    error = (
                        "Category or section is required."
                    )
                    break

                if not performance_date:
                    error = (
                        "Performance date is required."
                    )
                    break

                if _parse_date(performance_date) is None:
                    error = (
                        "Please enter a valid "
                        "performance date."
                    )
                    break

                if (
                    performance_time
                    and _parse_time(performance_time) is None
                ):
                    error = (
                        "Please enter a valid "
                        "performance time."
                    )
                    break

                cleaned_entries.append(
                    {
                        "dancer_name": dancer_name,
                        "dance_name": dance_name,
                        "category": category,
                        "event_number": event_number,
                        "performance_date": performance_date,
                        "performance_time": performance_time,
                    }
                )

        with session_factory() as session:
            event = _live_event(
                session,
                "theatre",
            )

            if event is None:
                return _not_found(templates, request)

            if error:
                return _form_response(
                    templates,
                    request,
                    event,
                    "theatre",
                    form_error=error,
                    form_data={
                        "customer_name": customer_name,
                        "customer_email": customer_email,
                        "customer_phone": customer_phone,
                        "entries_json": _clean(
                            form.get("entries_json")
                        ),
                    },
                    status_code=422,
                )

            unit_price_pence = 2300
            total_pence = (
                len(cleaned_entries)
                * unit_price_pence
            )

            booking = PersonalVideoBooking(
                event_id=event.id,
                reference=_reference(),
                manage_token=secrets.token_urlsafe(32),
                booking_type="theatre",
                customer_name=customer_name,
                customer_email=customer_email,
                customer_phone=customer_phone,
                status="payment_due",
                payment_status="awaiting_payment",
                initial_payment_pence=0,
                total_paid_pence=0,
                final_total_pence=total_pence,
                payment_method=(
                    "service_desk"
                    if payment_choice == "stand"
                    else "sumup_online"
                ),
            )

            session.add(booking)
            session.flush()

            for item in cleaned_entries:
                session.add(
                    PersonalVideoEntry(
                        booking_id=booking.id,
                        dancer_name=item["dancer_name"],
                        dance_name=item["dance_name"],
                        category=item["category"],
                        event_number=item["event_number"],
                        performance_date=_parse_date(
                            item["performance_date"]
                        ),
                        performance_time=_parse_time(
                            item["performance_time"]
                        ),
                        expected_video_count=1,
                        unit_price_pence=unit_price_pence,
                        status="awaiting_payment",
                    )
                )

            session.commit()
            session.refresh(booking)

            return _complete_personal_video_submission(
                templates,
                session,
                request,
                event,
                booking,
                payment_choice,
            )

    @router.get(
        "/personal-video/payment/{token}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_payment_return(
        request: Request,
        token: str,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(
                templates,
                request,
            )

        with session_factory() as session:
            booking = session.scalar(
                select(PersonalVideoBooking)
                .where(
                    PersonalVideoBooking.manage_token
                    == token
                )
                .limit(1)
            )

            if booking is None:
                return _not_found(
                    templates,
                    request,
                )

            event = session.get(
                Event,
                booking.event_id,
            )

            if event is None:
                return _not_found(
                    templates,
                    request,
                )

            try:
                status = (
                    _apply_personal_video_checkout(
                        session,
                        request.app.state.settings,
                        booking,
                    )
                )
            except SumUpError:
                return _personal_video_saved_response(
                    templates,
                    request,
                    event,
                    booking,
                    payment_error=(
                        "We could not verify the secure "
                        "payment. No unverified payment "
                        "has been applied to your booking."
                    ),
                )

            return _personal_video_saved_response(
                templates,
                request,
                event,
                booking,
                payment_result=status,
            )

    @router.post(
        "/api/payments/personal-video/{token}/sumup",
        include_in_schema=False,
    )
    async def personal_video_sumup_webhook(
        request: Request,
        token: str,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return Response(status_code=503)

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        checkout_id = str(
            payload.get("id")
            if isinstance(payload, dict)
            else ""
        ).strip()

        with session_factory() as session:
            booking = session.scalar(
                select(PersonalVideoBooking)
                .where(
                    PersonalVideoBooking.manage_token
                    == token
                )
                .limit(1)
            )

            if booking is None:
                return Response(status_code=204)

            attached = str(
                booking.sumup_checkout_id or ""
            ).strip()

            # Never allow an old or foreign SumUp checkout
            # notification to settle this booking.
            if (
                checkout_id
                and checkout_id != attached
            ):
                return Response(status_code=204)

            if not attached:
                return Response(status_code=204)

            try:
                _apply_personal_video_checkout(
                    session,
                    request.app.state.settings,
                    booking,
                )
            except SumUpError:
                # Ask SumUp to retry a genuine verification
                # failure without applying unverified money.
                return Response(status_code=503)

        return Response(status_code=204)


    def _manage_record(
        session,
        token: str,
    ):
        token = _clean(token, 120)

        if not token:
            return None, None, []

        booking = session.scalar(
            select(PersonalVideoBooking)
            .where(
                PersonalVideoBooking.manage_token
                == token
            )
            .limit(1)
        )

        if booking is None:
            return None, None, []

        event = session.get(
            Event,
            booking.event_id,
        )

        if event is None:
            return None, None, []

        entries = list(
            session.scalars(
                select(PersonalVideoEntry)
                .where(
                    PersonalVideoEntry.booking_id
                    == booking.id
                )
                .order_by(
                    PersonalVideoEntry.performance_date,
                    PersonalVideoEntry.performance_time,
                    PersonalVideoEntry.created_at,
                )
            )
        )

        return event, booking, entries

    def _manage_response(
        request: Request,
        event,
        booking,
        entries,
        *,
        saved: bool = False,
        entry_saved: str = "",
        form_error: str = "",
        status_code: int = 200,
    ):
        response = templates.TemplateResponse(
            request=request,
            name="personal_video_manage.html",
            context={
                "event": event,
                "booking": booking,
                "entries": entries,
                "booking_type_label": (
                    PERSONAL_VIDEO_TYPES.get(
                        booking.booking_type,
                        "Personal Video",
                    )
                ),
                "saved": saved,
                "entry_saved": entry_saved,
                "form_error": form_error,
                "freestyle_styles": FREESTYLE_STYLES,
                "freestyle_categories": (
                    FREESTYLE_CATEGORIES
                ),
                "freestyle_age_groups": (
                    FREESTYLE_AGE_GROUPS
                ),
            },
            status_code=status_code,
        )

        response.headers["Cache-Control"] = (
            "no-store, no-cache, "
            "must-revalidate, private"
        )

        return response

    @router.get(
        "/personal-video/manage/{token}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_manage(
        request: Request,
        token: str,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(
                templates,
                request,
            )

        with session_factory() as session:
            event, booking, entries = _manage_record(
                session,
                token,
            )

            if booking is None:
                return _not_found(
                    templates,
                    request,
                )

            return _manage_response(
                request,
                event,
                booking,
                entries,
            )

    @router.post(
        "/personal-video/manage/{token}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_manage_update(
        request: Request,
        token: str,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(
                templates,
                request,
            )

        form = await request.form()

        customer_name = _clean(
            form.get("customer_name"),
            200,
        )
        customer_email = _clean(
            form.get("customer_email"),
            320,
        ).lower()
        customer_phone = _clean(
            form.get("customer_phone"),
            80,
        )
        customer_notes = _clean(
            form.get("customer_notes"),
            2000,
        )

        competitor_number = _clean(
            form.get("competitor_number"),
            100,
        )
        outfit_description = _clean(
            form.get("outfit_description"),
            1000,
        )

        error = ""

        if not customer_name:
            error = "Customer name is required."
        elif not _valid_email(customer_email):
            error = (
                "A valid customer email address "
                "is required."
            )

        with session_factory() as session:
            event, booking, entries = _manage_record(
                session,
                token,
            )

            if booking is None:
                return _not_found(
                    templates,
                    request,
                )

            if error:
                return _manage_response(
                    request,
                    event,
                    booking,
                    entries,
                    form_error=error,
                    status_code=422,
                )

            booking.customer_name = customer_name
            booking.customer_email = customer_email
            booking.customer_phone = customer_phone
            booking.customer_notes = customer_notes

            if booking.booking_type == "ballroom":
                booking.competitor_number = (
                    competitor_number
                )
                booking.outfit_description = (
                    outfit_description
                )

            session.commit()
            session.refresh(booking)

            event, booking, entries = _manage_record(
                session,
                token,
            )

            return _manage_response(
                request,
                event,
                booking,
                entries,
                saved=True,
            )

    @router.post(
        "/personal-video/manage/{token}/entry/{entry_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def personal_video_manage_entry_update(
        request: Request,
        token: str,
        entry_id: str,
    ):
        session_factory = getattr(
            request.app.state,
            "session_factory",
            None,
        )

        if session_factory is None:
            return _not_found(
                templates,
                request,
            )

        form = await request.form()

        with session_factory() as session:
            event, booking, entries = _manage_record(
                session,
                token,
            )

            if booking is None:
                return _not_found(
                    templates,
                    request,
                )

            entry = session.scalar(
                select(PersonalVideoEntry)
                .where(
                    PersonalVideoEntry.id == entry_id,
                    PersonalVideoEntry.booking_id
                    == booking.id,
                )
                .limit(1)
            )

            if entry is None:
                return _not_found(
                    templates,
                    request,
                )

            error = ""

            if booking.booking_type == "ballroom":
                entry.event_number = _clean(
                    form.get("event_number"),
                    100,
                )
                entry.event_name = _clean(
                    form.get("event_name"),
                    240,
                )
                entry.dancer_number = _clean(
                    form.get("dancer_number"),
                    100,
                )

            elif booking.booking_type == "freestyle":
                dancer_name = _clean(
                    form.get("dancer_name"),
                    200,
                )
                dancer_number = _clean(
                    form.get("dancer_number"),
                    100,
                )
                dance_style = _clean(
                    form.get("dance_style"),
                    120,
                )
                category = _clean(
                    form.get("category"),
                    120,
                )
                age_group = _clean(
                    form.get("age_group"),
                    80,
                )
                performance_date_text = _clean(
                    form.get("performance_date"),
                    20,
                )
                performance_time_text = _clean(
                    form.get("performance_time"),
                    20,
                )

                performance_date = _parse_date(
                    performance_date_text
                )
                performance_time = _parse_time(
                    performance_time_text
                )

                if not dancer_number:
                    error = "Dancer number is required."
                elif not dancer_name:
                    error = "Dancer name is required."
                elif dance_style not in FREESTYLE_STYLES:
                    error = "Invalid Freestyle dance style."
                elif (
                    category
                    not in FREESTYLE_CATEGORIES
                ):
                    error = "Invalid Freestyle category."
                elif (
                    age_group
                    not in FREESTYLE_AGE_GROUPS
                ):
                    error = "Invalid Freestyle age group."
                elif not performance_date_text:
                    error = "Dance date is required."
                elif performance_date is None:
                    error = "Invalid dance date."
                elif (
                    performance_time_text
                    and performance_time is None
                ):
                    error = "Invalid dance time."

                if not error:
                    entry.dancer_name = dancer_name
                    entry.dancer_number = dancer_number
                    entry.dance_style = dance_style
                    entry.category = category
                    entry.age_group = age_group
                    entry.performance_date = (
                        performance_date
                    )
                    entry.performance_time = (
                        performance_time
                    )

            elif booking.booking_type == "theatre":
                dancer_name = _clean(
                    form.get("dancer_name"),
                    200,
                )
                dance_name = _clean(
                    form.get("dance_name"),
                    240,
                )
                category = _clean(
                    form.get("category"),
                    120,
                )
                event_number = _clean(
                    form.get("event_number"),
                    100,
                )
                performance_date_text = _clean(
                    form.get("performance_date"),
                    20,
                )
                performance_time_text = _clean(
                    form.get("performance_time"),
                    20,
                )

                performance_date = _parse_date(
                    performance_date_text
                )
                performance_time = _parse_time(
                    performance_time_text
                )

                if not dancer_name:
                    error = (
                        "Dancer or group name is required."
                    )
                elif not dance_name:
                    error = "Exact dance name is required."
                elif not category:
                    error = (
                        "Category or section is required."
                    )
                elif not performance_date_text:
                    error = (
                        "Performance date is required."
                    )
                elif performance_date is None:
                    error = (
                        "Please enter a valid "
                        "performance date."
                    )
                elif (
                    performance_time_text
                    and performance_time is None
                ):
                    error = (
                        "Please enter a valid "
                        "performance time."
                    )

                if not error:
                    entry.dancer_name = dancer_name
                    entry.dance_name = dance_name
                    entry.category = category
                    entry.event_number = event_number
                    entry.performance_date = (
                        performance_date
                    )
                    entry.performance_time = (
                        performance_time
                    )

            else:
                return _not_found(
                    templates,
                    request,
                )

            if error:
                return _manage_response(
                    request,
                    event,
                    booking,
                    entries,
                    form_error=error,
                    status_code=422,
                )

            session.commit()

            event, booking, entries = _manage_record(
                session,
                token,
            )

            return _manage_response(
                request,
                event,
                booking,
                entries,
                entry_saved=entry_id,
            )


    return router
