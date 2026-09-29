import os

os.environ.setdefault(
    "PIROUETTE_DATABASE_URL",
    "sqlite+pysqlite:///:memory:",
)
os.environ.setdefault(
    "PIROUETTE_SECRET_KEY",
    "r7-customer-access-test-secret-key",
)

from fastapi.testclient import TestClient

from app.main import create_app


def test_registration_page_renders():
    with TestClient(create_app()) as client:
        response = client.get(
            "/customer/register?next=/"
        )

        assert response.status_code == 200
        assert "View your event photos" in response.text
        assert "Email my verification code" in response.text


def test_registration_requires_valid_csrf():
    with TestClient(create_app()) as client:
        response = client.post(
            "/customer/register",
            data={
                "first_name": "Sophie",
                "last_name": "Customer",
                "email": "customer@example.com",
                "next_url": "/",
                "csrf_token": "wrong",
            },
        )

        assert response.status_code == 400
        assert "form expired" in response.text.lower()


def test_fake_sender_receives_six_digit_code():
    sent = []

    def fake_sender(settings, email, code):
        sent.append((email, code))

    app = create_app()
    app.state.customer_code_sender = fake_sender

    with TestClient(app) as client:
        page = client.get("/customer/register")
        assert page.status_code == 200

        csrf = client.cookies.get(
            "pirouette_customer_session"
        )
        assert csrf

        import re

        match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"',
            page.text,
        )
        assert match

        response = client.post(
            "/customer/register",
            data={
                "first_name": "Sophie",
                "last_name": "Customer",
                "email": "Customer@Example.COM",
                "next_url": "/customer-test-destination",
                "csrf_token": match.group(1),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == (
            "/customer/verify"
        )

        assert len(sent) == 1
        assert sent[0][0] == "customer@example.com"
        assert len(sent[0][1]) == 6
        assert sent[0][1].isdigit()


def test_complete_fake_otp_flow_sets_verified_session():
    sent = []

    def fake_sender(settings, email, code):
        sent.append((email, code))

    app = create_app()
    app.state.customer_code_sender = fake_sender

    with TestClient(app) as client:
        registration = client.get(
            "/customer/register"
        )

        import re

        registration_csrf = re.search(
            r'name="csrf_token"\s+value="([^"]+)"',
            registration.text,
        ).group(1)

        response = client.post(
            "/customer/register",
            data={
                "first_name": "Sophie",
                "last_name": "Customer",
                "email": "customer@example.com",
                "next_url": "/",
                "csrf_token": registration_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert len(sent) == 1

        verification = client.get(
            "/customer/verify"
        )

        assert verification.status_code == 200
        assert "customer@example.com" in verification.text

        verification_csrf = re.search(
            r'name="csrf_token"\s+value="([^"]+)"',
            verification.text,
        ).group(1)

        response = client.post(
            "/customer/verify",
            data={
                "code": sent[0][1],
                "csrf_token": verification_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == (
            "/customer/consent"
        )

        consent_page = client.get(
            "/customer/consent"
        )

        assert consent_page.status_code == 200
        assert "Before you view your photographs" in (
            consent_page.text
        )

        consent_csrf = re.search(
            r'name="csrf_token"\s+value="([^"]+)"',
            consent_page.text,
        ).group(1)

        response = client.post(
            "/customer/consent",
            data={
                "accept_terms": "yes",
                "accept_privacy": "yes",
                "accept_copyright": "yes",
                "csrf_token": consent_csrf,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/"


def test_external_next_url_is_rejected():
    with TestClient(create_app()) as client:
        response = client.get(
            "/customer/register"
            "?next=https://example.com/bad"
        )

        assert response.status_code == 200
        assert (
            'name="next_url"\n        value="/"'
            in response.text
        )


def test_customer_sender_is_wired_without_opening_smtp(
    monkeypatch,
):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Creating the app must not open SMTP"
        )

    monkeypatch.setattr(
        "smtplib.SMTP",
        forbidden,
    )

    monkeypatch.setattr(
        "smtplib.SMTP_SSL",
        forbidden,
    )

    app = create_app()

    assert hasattr(
        app.state,
        "customer_code_sender",
    )

    assert callable(
        app.state.customer_code_sender
    )
