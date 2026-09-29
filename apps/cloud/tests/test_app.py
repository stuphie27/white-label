from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def login(client: TestClient) -> None:
    page = client.get("/staff/login")
    marker = 'name="csrf_token" value="'
    token = page.text.split(marker, 1)[1].split('"', 1)[0]
    response = client.post(
        "/staff/login",
        data={
            "email": "admin@sophies.photography",
            "password": "Correct-Horse-Battery-2026",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def event_csrf(html: str) -> str:
    marker = 'name="csrf_token" value="'
    return html.split(marker, 1)[1].split('"', 1)[0]


def test_home_health_and_readiness():
    with TestClient(create_app()) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Pirouette Cloud" in home.text
        assert client.get("/healthz").json()["ok"] is True
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["database"] == "ready"


def test_staff_dashboard_requires_login():
    with TestClient(create_app()) as client:
        response = client.get("/staff", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/staff/login"


def test_staff_can_login_and_logout():
    with TestClient(create_app()) as client:
        login(client)
        dashboard = client.get("/staff")
        assert dashboard.status_code == 200
        assert "Signed in as admin@sophies.photography" in dashboard.text
        logout = client.post("/staff/logout", follow_redirects=False)
        assert logout.status_code == 303
        assert client.get("/staff", follow_redirects=False).status_code == 303


def test_event_crud_flow():
    with TestClient(create_app()) as client:
        login(client)
        new_page = client.get("/staff/events/new")
        assert new_page.status_code == 200
        create = client.post(
            "/staff/events/new",
            data={
                "name": "Freestyle Championship",
                "start_date": "2026-08-02",
                "end_date": "2026-08-02",
                "venue": "Test Arena",
                "description": "Test event",
                "internal_notes": "Staff only",
                "event_status": "draft",
                "csrf_token": event_csrf(new_page.text),
            },
            follow_redirects=False,
        )
        assert create.status_code == 303
        assert create.headers["location"].startswith("/staff/events/")
        detail_path = create.headers["location"]
        detail = client.get(detail_path)
        assert "Freestyle Championship" in detail.text
        assert "Test Arena" in detail.text

        edit_page = client.get(detail_path + "/edit")
        updated = client.post(
            detail_path + "/edit",
            data={
                "name": "Freestyle Championship Updated",
                "start_date": "2026-08-02",
                "end_date": "2026-08-03",
                "venue": "Main Arena",
                "description": "Updated",
                "internal_notes": "Updated notes",
                "event_status": "live",
                "csrf_token": event_csrf(edit_page.text),
            },
            follow_redirects=False,
        )
        assert updated.status_code == 303
        assert "Freestyle Championship Updated" in client.get(detail_path).text

        detail = client.get(detail_path)
        archived = client.post(
            detail_path + "/archive",
            data={"csrf_token": event_csrf(detail.text)},
            follow_redirects=False,
        )
        assert archived.status_code == 303
        assert "Archived" in client.get(detail_path).text


def test_invalid_event_dates_are_rejected():
    with TestClient(create_app()) as client:
        login(client)
        page = client.get("/staff/events/new")
        response = client.post(
            "/staff/events/new",
            data={
                "name": "Bad dates",
                "start_date": "2026-08-05",
                "end_date": "2026-08-04",
                "venue": "",
                "description": "",
                "internal_notes": "",
                "event_status": "draft",
                "csrf_token": event_csrf(page.text),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=" in response.headers["location"]


def test_public_and_event_hardware_routes_remain_unavailable():
    with TestClient(create_app()) as client:
        for path in ("/dashboard", "/super-admin", "/finance", "/kiosk", "/gallery", "/downloads"):
            assert client.get(path).status_code == 404


def test_dashboard_survives_uninitialised_schema():
    from sqlalchemy import MetaData

    with TestClient(create_app()) as client:
        login(client)
        engine = client.app.state.engine
        metadata = MetaData()
        metadata.reflect(bind=engine)
        metadata.drop_all(bind=engine)

        dashboard = client.get("/staff")
        assert dashboard.status_code == 200
        assert "Database connected — setup required" in dashboard.text

        events = client.get("/staff/events")
        assert events.status_code == 503
        assert "Database setup is required" in events.text


def test_staff_can_initialise_database_schema():
    from sqlalchemy import MetaData, inspect

    with TestClient(create_app()) as client:
        login(client)
        engine = client.app.state.engine
        metadata = MetaData()
        metadata.reflect(bind=engine)
        metadata.drop_all(bind=engine)

        page = client.get("/staff/database")
        assert page.status_code == 200
        assert "Initialise Database" in page.text
        token = event_csrf(page.text)
        response = client.post(
            "/staff/database/initialise",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 303
        tables = set(inspect(engine).get_table_names())
        assert {"schema_migrations", "events"}.issubset(tables)


def test_dashboard_uses_pirouette_live_control_design():
    with TestClient(create_app()) as client:
        login(client)
        dashboard = client.get('/staff')
        assert dashboard.status_code == 200
        assert 'LIVE CLOUD CONTROL' in dashboard.text
        assert 'QUICK ACTIONS' in dashboard.text
        assert 'OPEN SYSTEM HEALTH' in dashboard.text


def test_event_search_and_status_filter():
    with TestClient(create_app()) as client:
        login(client)
        first = client.get('/staff/events/new')
        client.post(
            '/staff/events/new',
            data={
                'name': 'London Freestyle Finals',
                'start_date': '2026-09-01',
                'end_date': '2026-09-01',
                'venue': 'London Arena',
                'description': '',
                'internal_notes': '',
                'event_status': 'live',
                'csrf_token': event_csrf(first.text),
            },
            follow_redirects=False,
        )
        second = client.get('/staff/events/new')
        client.post(
            '/staff/events/new',
            data={
                'name': 'Manchester Test Event',
                'start_date': '2026-09-02',
                'end_date': '2026-09-02',
                'venue': 'North Hall',
                'description': '',
                'internal_notes': '',
                'event_status': 'draft',
                'csrf_token': event_csrf(second.text),
            },
            follow_redirects=False,
        )
        searched = client.get('/staff/events?q=London')
        assert 'London Freestyle Finals' in searched.text
        assert 'Manchester Test Event' not in searched.text
        filtered = client.get('/staff/events?status_filter=draft')
        assert 'Manchester Test Event' in filtered.text
        assert 'London Freestyle Finals' not in filtered.text


def test_event_operational_fields_and_search():
    with TestClient(create_app()) as client:
        login(client)
        page = client.get('/staff/events/new')
        response = client.post(
            '/staff/events/new',
            data={
                'name': 'Southern Freestyle Finals',
                'client_name': 'Starlight Dance Academy',
                'principal_name': 'Jane Principal',
                'photographer_name': 'Stuart Perren',
                'dance_style': 'Freestyle',
                'start_date': '2026-09-12',
                'end_date': '2026-09-12',
                'arrival_time': '07:30',
                'photography_start': '08:30',
                'photography_finish': '17:00',
                'venue': 'South Arena',
                'description': '',
                'internal_notes': 'Bring spare printer media',
                'event_status': 'draft',
                'csrf_token': event_csrf(page.text),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers['location'])
        assert 'Starlight Dance Academy' in detail.text
        assert 'Jane Principal' in detail.text
        assert 'Stuart Perren' in detail.text
        assert '08:30' in detail.text
        search = client.get('/staff/events?q=Stuart')
        assert 'Southern Freestyle Finals' in search.text


def test_photography_finish_before_start_is_rejected():
    with TestClient(create_app()) as client:
        login(client)
        page = client.get('/staff/events/new')
        response = client.post(
            '/staff/events/new',
            data={
                'name': 'Invalid timings',
                'start_date': '2026-09-12',
                'end_date': '2026-09-12',
                'photography_start': '17:00',
                'photography_finish': '08:30',
                'event_status': 'draft',
                'csrf_token': event_csrf(page.text),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert 'error=' in response.headers['location']


def test_event_production_checklist_updates_and_dashboard_summary():
    with TestClient(create_app()) as client:
        login(client)
        page = client.get('/staff/events/new')
        created = client.post(
            '/staff/events/new',
            data={
                'name': 'Checklist Event',
                'client_name': 'Checklist Dance School',
                'start_date': '2026-09-20',
                'end_date': '2026-09-20',
                'event_status': 'draft',
                'csrf_token': event_csrf(page.text),
            },
            follow_redirects=False,
        )
        detail_path = created.headers['location']
        detail = client.get(detail_path)
        assert 'PRODUCTION CHECKLIST' in detail.text
        assert '0/7 complete' in detail.text

        checklist_tasks = (
            'booking_confirmed',
            'contract_received',
            'photographer_assigned',
            'equipment_packed',
            'galleries_uploaded',
            'gallery_published',
            'orders_complete',
        )
        for task in checklist_tasks:
            detail = client.get(detail_path)
            response = client.post(
                detail_path + '/checklist',
                data={
                    'task': task,
                    'completed': '1',
                    'csrf_token': event_csrf(detail.text),
                },
                follow_redirects=False,
            )
            assert response.status_code == 303

        detail = client.get(detail_path)
        assert '7/7 complete' in detail.text
        assert '100% ready' in detail.text
        dashboard = client.get('/staff')
        assert 'Production Ready' in dashboard.text
        assert 'Checklist complete' in dashboard.text


def test_invalid_checklist_task_is_ignored():
    with TestClient(create_app()) as client:
        login(client)
        page = client.get('/staff/events/new')
        created = client.post(
            '/staff/events/new',
            data={
                'name': 'Checklist Security Event',
                'start_date': '2026-09-21',
                'end_date': '2026-09-21',
                'event_status': 'draft',
                'csrf_token': event_csrf(page.text),
            },
            follow_redirects=False,
        )
        detail_path = created.headers['location']
        detail = client.get(detail_path)
        response = client.post(
            detail_path + '/checklist',
            data={
                'task': 'name',
                'completed': '1',
                'csrf_token': event_csrf(detail.text),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert 'Checklist Security Event' in client.get(detail_path).text


def test_gallery_staff_flow_and_public_access():
    with TestClient(create_app()) as client:
        login(client)
        event_page = client.get('/staff/events/new')
        event_create = client.post(
            '/staff/events/new',
            data={
                'name': 'Gallery Test Event',
                'start_date': '2026-10-03',
                'end_date': '2026-10-03',
                'event_status': 'draft',
                'csrf_token': event_csrf(event_page.text),
            },
            follow_redirects=False,
        )
        event_id = event_create.headers['location'].rsplit('/', 1)[-1]
        gallery_page = client.get(f'/staff/galleries/new?event_id={event_id}')
        assert gallery_page.status_code == 200
        gallery_create = client.post(
            '/staff/galleries/new',
            data={
                'event_id': event_id,
                'name': 'Gallery Test Event Photos',
                'slug': 'gallery-test-event',
                'gallery_status': 'published',
                'visibility': 'private',
                'access_code': '246810',
                'expires_at': '2026-12-01',
                'description': 'Your event photographs.',
                'csrf_token': event_csrf(gallery_page.text),
            },
            follow_redirects=False,
        )
        assert gallery_create.status_code == 303
        detail = client.get(gallery_create.headers['location'])
        assert detail.status_code == 200
        assert 'Gallery Test Event Photos' in detail.text
        public = client.get('/g/gallery-test-event')
        assert public.status_code == 200
        assert 'Access code' in public.text
        wrong = client.post('/g/gallery-test-event/unlock', data={'access_code': '000000'}, follow_redirects=False)
        assert 'error=' in wrong.headers['location']
        unlocked = client.post('/g/gallery-test-event/unlock', data={'access_code': '246810'}, follow_redirects=False)
        assert unlocked.status_code == 303
        assert 'Your gallery is ready' in client.get('/g/gallery-test-event').text


def test_gallery_slug_is_unique():
    from app.gallery.service import create_gallery
    from app.db.models import Event
    from datetime import date

    with TestClient(create_app()) as client:
        with client.app.state.session_factory() as session:
            event = Event(name='Slug Event', start_date=date(2026, 10, 4), end_date=date(2026, 10, 4))
            session.add(event)
            session.commit()
            session.refresh(event)
            first = create_gallery(session, event_id=event.id, name='My Gallery', slug='my-gallery', status='draft', visibility='public', access_code='', expires_at=None, description='')
            second = create_gallery(session, event_id=event.id, name='My Gallery', slug='my-gallery', status='draft', visibility='public', access_code='', expires_at=None, description='')
            assert first.slug == 'my-gallery'
            assert second.slug == 'my-gallery-2'


def test_transfer_queue_foundation_and_five_day_expiry():
    from datetime import date, timedelta
    from app.db.models import Event
    from app.gallery.service import create_gallery
    from app.transfers.service import MAX_RETENTION_DAYS, create_transfer_job

    with TestClient(create_app()) as client:
        login(client)
        with client.app.state.session_factory() as session:
            event = Event(name='Transfer Event', start_date=date(2026, 10, 5), end_date=date(2026, 10, 5))
            session.add(event)
            session.commit()
            session.refresh(event)
            gallery = create_gallery(session, event_id=event.id, name='Transfer Gallery', slug='transfer-gallery', status='ready', visibility='private', access_code='123456', expires_at=None, description='')
            job = create_transfer_job(session, gallery_id=gallery.id, destination='customer', item_count=18)
            assert job.item_count == 18
            assert job.status == 'waiting'
            assert job.expires_at - job.created_at <= timedelta(days=MAX_RETENTION_DAYS, seconds=1)
            job_id = job.id

        queue = client.get('/staff/transfers')
        assert queue.status_code == 200
        assert 'Transfer Gallery' in queue.text
        detail = client.get(f'/staff/transfers/{job_id}')
        assert detail.status_code == 200
        assert 'Customer' in detail.text
        assert 'five days' in queue.text.lower()


def test_transfer_status_update():
    from datetime import date
    from app.db.models import Event
    from app.gallery.service import create_gallery
    from app.transfers.service import create_transfer_job

    with TestClient(create_app()) as client:
        login(client)
        with client.app.state.session_factory() as session:
            event = Event(name='Zenfolio Event', start_date=date(2026, 10, 6), end_date=date(2026, 10, 6))
            session.add(event)
            session.commit()
            session.refresh(event)
            gallery = create_gallery(session, event_id=event.id, name='Zenfolio Gallery', slug='zenfolio-gallery', status='ready', visibility='private', access_code='', expires_at=None, description='')
            job = create_transfer_job(session, gallery_id=gallery.id, destination='zenfolio', item_count=42)
            job_id = job.id

        detail = client.get(f'/staff/transfers/{job_id}')
        response = client.post(
            f'/staff/transfers/{job_id}/status',
            data={'transfer_status': 'uploading', 'csrf_token': event_csrf(detail.text)},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert 'Uploading' in client.get(f'/staff/transfers/{job_id}').text


def sync_headers() -> dict[str, str]:
    return {"X-Pirouette-Sync-Key": "test-sync-api-key-2026"}


def test_sync_api_rejects_missing_key():
    with TestClient(create_app()) as client:
        response = client.get("/api/sync/v1/status")
        assert response.status_code == 401


def test_sync_api_event_gallery_transfer_and_resume_flow():
    with TestClient(create_app()) as client:
        status = client.get("/api/sync/v1/status", headers=sync_headers())
        assert status.status_code == 200
        assert status.json()["retention_days"] == 5
        assert status.json()["stores_files"] is False

        event = client.put(
            "/api/sync/v1/events/event-local-001",
            headers=sync_headers(),
            json={
                "name": "Offline Sync Test Event",
                "client_name": "Test Dance School",
                "start_date": "2026-11-01",
                "end_date": "2026-11-01",
                "venue": "Test Hall",
                "status": "draft",
            },
        )
        assert event.status_code == 200
        assert event.json()["source_ref"] == "event-local-001"

        gallery = client.put(
            "/api/sync/v1/galleries/gallery-local-001",
            headers=sync_headers(),
            json={
                "event_source_ref": "event-local-001",
                "name": "Offline Sync Gallery",
                "slug": "offline-sync-gallery",
                "status": "draft",
                "visibility": "private",
            },
        )
        assert gallery.status_code == 200
        assert gallery.json()["source_ref"] == "gallery-local-001"

        transfer = client.post(
            "/api/sync/v1/transfers",
            headers=sync_headers(),
            json={
                "source_ref": "transfer-local-001",
                "gallery_source_ref": "gallery-local-001",
                "destination": "customer",
            },
        )
        assert transfer.status_code == 200
        assert transfer.json()["status"] == "waiting"

        manifest = client.put(
            "/api/sync/v1/transfers/transfer-local-001/manifest",
            headers=sync_headers(),
            json={"assets": [
                {"source_ref": "asset-1", "filename": "IMG_0001.jpg", "size_bytes": 1000, "sha256": "a" * 64},
                {"source_ref": "asset-2", "filename": "IMG_0002.jpg", "size_bytes": 2000, "sha256": "b" * 64},
            ]},
        )
        assert manifest.status_code == 200
        assert manifest.json()["item_count"] == 2
        assert manifest.json()["bytes_total"] == 3000

        progress = client.patch(
            "/api/sync/v1/transfers/transfer-local-001/assets/asset-1",
            headers=sync_headers(),
            json={"bytes_received": 600, "status": "uploading"},
        )
        assert progress.status_code == 200
        assert progress.json()["bytes_received"] == 600

        resume = client.get(
            "/api/sync/v1/transfers/transfer-local-001/resume",
            headers=sync_headers(),
        )
        assert resume.status_code == 200
        body = resume.json()
        assert body["transfer"]["bytes_total"] == 3000
        assert body["transfer"]["bytes_transferred"] == 600
        assert len(body["assets"]) == 2


def test_sync_upserts_are_idempotent():
    with TestClient(create_app()) as client:
        payload = {
            "name": "Idempotent Event",
            "start_date": "2026-12-01",
            "end_date": "2026-12-01",
            "status": "draft",
        }
        first = client.put("/api/sync/v1/events/same-event", headers=sync_headers(), json=payload)
        second = client.put("/api/sync/v1/events/same-event", headers=sync_headers(), json={**payload, "name": "Updated Event"})
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
