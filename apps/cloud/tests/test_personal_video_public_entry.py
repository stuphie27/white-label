from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ROUTER = (
    ROOT
    / "app"
    / "personal_video"
    / "router.py"
).read_text(encoding="utf-8")

MAIN = (
    ROOT
    / "app"
    / "main.py"
).read_text(encoding="utf-8")

TEMPLATE = (
    ROOT
    / "app"
    / "templates"
    / "personal_video_online.html"
).read_text(encoding="utf-8")


def test_public_personal_video_route_exists():
    assert '"/personal-video"' in ROUTER
    assert 'Event.status == "live"' in ROUTER
    assert (
        "Event.personal_video_enabled.is_(True)"
        in ROUTER
    )


def test_public_entry_supports_all_three_types():
    assert '"ballroom": "Ballroom"' in ROUTER
    assert '"freestyle": "Freestyle"' in ROUTER
    assert '"theatre": "Theatre"' in ROUTER


def test_invalid_personal_video_type_is_not_exposed():
    assert "not in PERSONAL_VIDEO_TYPES" in ROUTER
    assert "status_code=404" in ROUTER


def test_public_personal_video_is_not_cached():
    assert (
        'response.headers["Cache-Control"] = "no-store"'
        in ROUTER
    )


def test_personal_video_router_is_registered():
    assert (
        "from app.personal_video.router import "
        "build_personal_video_router"
        in MAIN
    )
    assert (
        "build_personal_video_router(TEMPLATES)"
        in MAIN
    )


def test_online_entry_uses_customer_layout():
    assert (
        '{% extends "customer_base.html" %}'
        in TEMPLATE
    )
    assert (
        "{{ booking_type_label }} Personal Video"
        in TEMPLATE
    )
    assert (
        "Book your performance videos online"
        in TEMPLATE
    )
