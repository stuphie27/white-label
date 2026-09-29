from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = (ROOT / "app/checkout/public.py").read_text()


def test_checkout_imports_verified_customer_ownership():
    assert "favourite_session_for_verified_customer" in PUBLIC
    assert "verified_customer_profile" in PUBLIC


def test_favourites_page_prefers_verified_customer():
    marker = "async def favourites_page"
    section = PUBLIC.split(marker, 1)[1].split(
        '@router.get("/g/{slug}/favourites/open/{token}"',
        1,
    )[0]
    assert "favourite_session_for_verified_customer" in section
    assert "create=False" in section
    assert "_persistent_favourite_session" in section


def test_favourites_session_uses_verified_identity_before_submitted_email():
    marker = "async def open_favourite_session"
    section = PUBLIC.split(marker, 1)[1].split(
        '@router.post("/g/{slug}/favourites/{asset_id}"',
        1,
    )[0]

    verified_at = section.index("verified_customer_profile")
    submitted_email_at = section.index(
        "_normalise_email(customer_email)"
    )

    assert verified_at < submitted_email_at
    assert "favourite_session_for_verified_customer" in section
    assert "create=True" in section


def test_verified_favourite_toggle_creates_or_reconnects_owned_session():
    marker = "async def toggle_favourite"
    section = PUBLIC.split(marker, 1)[1].split(
        '@router.post("/g/{slug}/basket/add"',
        1,
    )[0]

    assert "favourite_session_for_verified_customer" in section
    assert "create=True" in section
    assert "_persistent_favourite_session" in section


def test_legacy_favourites_path_remains_available():
    assert "_normalise_email(customer_email)" in PUBLIC
    assert "CustomerFavouriteSession.gallery_id == gallery.id" in PUBLIC
    assert "CustomerFavouriteSession.email == email" in PUBLIC
    assert "reopen_only" in PUBLIC


def test_verified_customer_state_is_available_to_gallery_template():
    template = (
        ROOT / "app/templates/public_gallery.html"
    ).read_text()

    assert 'data-verified-customer=' in template
    assert "verified_customer" in template


def test_verified_toggle_does_not_require_submitted_profile():
    marker = "async def toggle_favourite"
    section = PUBLIC.split(marker, 1)[1].split(
        '@router.post("/g/{slug}/basket/add"',
        1,
    )[0]

    verified_at = section.index(
        "favourite_session_for_verified_customer"
    )
    legacy_at = section.index(
        "_persistent_favourite_session"
    )

    assert verified_at < legacy_at
    assert "create=True" in section
