from fastapi.testclient import TestClient

from app.main import create_app


def test_favourites_route_precedes_generic_gallery_route():
    app = create_app()

    paths = [
        getattr(route, "path", "")
        for route in app.routes
    ]

    favourites_index = paths.index("/g/{slug}/favourites")
    gallery_index = paths.index("/g/{slug}")

    assert favourites_index < gallery_index


def test_unknown_gallery_slug_returns_404_not_server_error():
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/g/r7a5-gallery-that-does-not-exist",
            follow_redirects=False,
        )

    assert response.status_code == 404
