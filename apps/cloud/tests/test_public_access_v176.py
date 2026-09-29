from fastapi.testclient import TestClient

from app.main import create_app

HEADERS = {"X-Pirouette-Sync-Key": "test-sync-api-key-2026"}


def _seed(client: TestClient):
    event = client.put(
        "/api/sync/v1/events/event-176",
        headers=HEADERS,
        json={
            "name": "Fareham Live",
            "start_date": "2026-08-07",
            "end_date": "2026-08-13",
            "venue": "Fareham",
            "status": "live",
        },
    )
    assert event.status_code == 200, event.text
    gallery = client.put(
        "/api/sync/v1/galleries/gallery-176",
        headers=HEADERS,
        json={
            "event_source_ref": "event-176",
            "name": "Fareham Live",
            "slug": "fareham-live",
            "status": "ready",
            "visibility": "public",
            "expires_at": "2026-08-20",
        },
    )
    assert gallery.status_code == 200, gallery.text


def test_customer_access_can_be_enabled_and_disabled_without_uploading_again():
    with TestClient(create_app()) as client:
        _seed(client)
        online = client.post("/api/sync/v1/galleries/gallery-176/online", headers=HEADERS)
        assert online.status_code == 200
        assert online.json()["status"] == "published"
        health = client.get("/api/sync/v1/galleries/gallery-176/health", headers=HEADERS)
        assert health.status_code == 200
        assert health.json()["gallery_status"] == "published"

        offline = client.post("/api/sync/v1/galleries/gallery-176/offline", headers=HEADERS)
        assert offline.status_code == 200
        assert offline.json()["status"] == "offline"
        health = client.get("/api/sync/v1/galleries/gallery-176/health", headers=HEADERS)
        assert health.json()["gallery_status"] == "ready"
