from datetime import date

import pytest
from pydantic import ValidationError

from app.db.models import Event
from app.sync.router import EventSync


def test_cloud_event_model_has_personal_video_settings():
    columns = Event.__table__.columns

    assert "personal_video_enabled" in columns
    assert "personal_video_type" in columns


@pytest.mark.parametrize(
    "booking_type",
    [
        "ballroom",
        "freestyle",
        "theatre",
    ],
)
def test_event_sync_accepts_personal_video_types(
    booking_type,
):
    payload = EventSync(
        name="Personal Video Event",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 19),
        personal_video_enabled=True,
        personal_video_type=booking_type,
    )

    values = payload.model_dump()

    assert values["personal_video_enabled"] is True
    assert values["personal_video_type"] == booking_type


def test_event_sync_defaults_personal_video_off():
    payload = EventSync(
        name="Ordinary Event",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 19),
    )

    assert payload.personal_video_enabled is False
    assert payload.personal_video_type == ""


def test_event_sync_rejects_unknown_personal_video_type():
    with pytest.raises(ValidationError):
        EventSync(
            name="Bad Personal Video Event",
            start_date=date(2026, 8, 19),
            end_date=date(2026, 8, 19),
            personal_video_enabled=True,
            personal_video_type="unknown",
        )
