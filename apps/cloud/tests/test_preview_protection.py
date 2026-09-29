from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_gallery_preview_is_not_cacheable():
    source = (ROOT / "app/gallery/router.py").read_text()

    assert 'headers={"Cache-Control": "private, no-store"},' in source
    assert 'headers={"Cache-Control": "private, max-age=3600"},' not in source


def test_spaces_gallery_preview_remains_short_lived_and_private():
    source = (ROOT / "app/gallery/router.py").read_text()

    assert "expires_seconds=300" in source
    assert 'headers={"Cache-Control": "private, no-store"},' in source


def test_preview_route_requires_gallery_unlock():
    source = (ROOT / "app/gallery/router.py").read_text()

    start = source.index('@router.get("/g/{slug}/assets/{asset_id}"')
    end = source.index('@router.post("/g/{slug}/unlock"', start)
    route = source[start:end]

    assert 'request.session.get(f"gallery_access_{gallery.id}") is True' in route
    assert 'if not unlocked:' in route


def test_customer_notice_states_copyright_and_session_link():
    template = (ROOT / "app/templates/public_gallery.html").read_text()

    assert "Copyright protected." in template
    assert "Preview access is linked to your viewing session." in template
    assert "Unauthorised screenshots" in template
    assert "copyright enforcement action" in template


def test_customer_notice_does_not_claim_forensic_tracing():
    template = (ROOT / "app/templates/public_gallery.html").read_text().lower()

    assert "invisible forensic" not in template
    assert "screenshots can be traced" not in template
    assert "screenshots are traceable" not in template
