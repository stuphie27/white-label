from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ROUTER = (
    ROOT
    / "app"
    / "personal_video"
    / "router.py"
).read_text(encoding="utf-8")

ENTRY = (
    ROOT
    / "app"
    / "templates"
    / "personal_video_online.html"
).read_text(encoding="utf-8")

BALLROOM = (
    ROOT
    / "app"
    / "templates"
    / "personal_video_ballroom.html"
).read_text(encoding="utf-8")

FREESTYLE = (
    ROOT
    / "app"
    / "templates"
    / "personal_video_freestyle.html"
).read_text(encoding="utf-8")

THEATRE = (
    ROOT
    / "app"
    / "templates"
    / "personal_video_theatre.html"
).read_text(encoding="utf-8")


def test_cloud_has_all_three_personal_video_get_routes():
    assert '"/personal-video/ballroom"' in ROUTER
    assert '"/personal-video/freestyle"' in ROUTER
    assert '"/personal-video/theatre"' in ROUTER

    assert (
        "Event.personal_video_type == booking_type"
        in ROUTER
    )


def test_entry_page_links_to_active_booking_type():
    assert (
        'href="/personal-video/{{ booking_type }}"'
        in ENTRY
    )


def test_all_forms_use_cloud_customer_layout():
    for template in (
        BALLROOM,
        FREESTYLE,
        THEATRE,
    ):
        assert (
            '{% extends "customer_base.html" %}'
            in template
        )


        assert 'href="/personal-video"' in template
        assert 'href="/kiosk/' not in template


def test_online_forms_now_submit_to_cloud_routes():
    assert (
        'action="/personal-video/ballroom"'
        in BALLROOM
    )
    assert (
        'action="/personal-video/freestyle"'
        in FREESTYLE
    )
    assert (
        'action="/personal-video/theatre"'
        in THEATRE
    )

    for template in (
        BALLROOM,
        FREESTYLE,
        THEATRE,
    ):
        assert (
            'data-cloud-render-only="true"'
            not in template
        )
        assert (
            "cloud-personal-video-render-message"
            not in template
        )

    assert ROUTER.count("@router.post(") == 6

    for route in (
        '"/personal-video/ballroom"',
        '"/personal-video/freestyle"',
        '"/personal-video/theatre"',
        '"/personal-video/manage/{token}"',
        '"/personal-video/manage/{token}/entry/{entry_id}"',
    ):
        assert route in ROUTER


def test_ballroom_contract_is_preserved():
    assert 'name="customer_name"' in BALLROOM
    assert 'name="customer_email"' in BALLROOM
    assert 'name="customer_phone"' in BALLROOM
    assert 'name="minimum_video_count"' in BALLROOM
    assert 'name="video_format"' in BALLROOM
    assert 'name="terms_accepted"' in BALLROOM
    assert 'name="entries_json"' in BALLROOM

    assert 'data-field="event_number"' in BALLROOM
    assert 'data-field="event_name"' in BALLROOM
    assert 'data-field="performance_date"' in BALLROOM
    assert 'data-field="performance_time"' in BALLROOM
    assert 'data-field="dancer_number"' in BALLROOM

    assert (
        'data-field="expected_video_count"'
        in BALLROOM
    )

    assert (
        'data-field="filming_instruction"'
        in BALLROOM
    )


def test_freestyle_contract_is_preserved():
    assert 'name="customer_name"' in FREESTYLE
    assert 'name="customer_email"' in FREESTYLE
    assert 'name="customer_phone"' in FREESTYLE
    assert 'name="entries_json"' in FREESTYLE

    assert 'data-field="dancer_name"' in FREESTYLE
    assert 'data-field="dancer_number"' in FREESTYLE
    assert 'data-field="dance_style"' in FREESTYLE
    assert 'data-field="category"' in FREESTYLE
    assert 'data-field="age_group"' in FREESTYLE

    assert (
        'data-field="performance_date"'
        in FREESTYLE
    )

    assert (
        'data-field="performance_time"'
        in FREESTYLE
    )


def test_theatre_contract_is_preserved():
    assert 'name="customer_name"' in THEATRE
    assert 'name="customer_email"' in THEATRE
    assert 'name="customer_phone"' in THEATRE
    assert 'name="entries_json"' in THEATRE

    assert 'data-field="dancer_name"' in THEATRE
    assert 'data-field="dance_name"' in THEATRE
    assert 'data-field="category"' in THEATRE
    assert 'data-field="event_number"' in THEATRE

    assert (
        'data-field="performance_date"'
        in THEATRE
    )

    assert (
        'data-field="performance_time"'
        in THEATRE
    )


def test_online_forms_are_no_store():
    assert (
        'response.headers["Cache-Control"] = '
        '"no-store"'
        in ROUTER
    )
