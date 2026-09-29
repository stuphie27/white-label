from types import SimpleNamespace

from app.customer_verification_email import (
    build_customer_verification_message,
)


def settings():
    return SimpleNamespace(
        smtp_from_name="Sophie’s Photography",
        smtp_from_email="photos@sophiesphotography.co.uk",
        smtp_username="photos@sophiesphotography.co.uk",
    )


def test_verification_email_headers():
    message = build_customer_verification_message(
        settings(),
        "customer@example.com",
        "123456",
    )

    assert message["To"] == "customer@example.com"
    assert "123456" in message["Subject"]
    assert "Sophie" in message["From"]


def test_verification_email_contains_code():
    message = build_customer_verification_message(
        settings(),
        "customer@example.com",
        "654321",
    )

    plain = message.get_body(
        preferencelist=("plain",)
    )

    html = message.get_body(
        preferencelist=("html",)
    )

    assert plain is not None
    assert html is not None

    plain_body = plain.get_content()
    html_body = html.get_content()

    assert "654321" in plain_body
    assert "10 minutes" in plain_body
    assert "Sophie" in plain_body

    assert "654321" in html_body
    assert "10 minutes" in html_body
    assert "SOPHIE" in html_body


def test_builder_does_not_open_smtp(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "SMTP must not be opened by email builder"
        )

    monkeypatch.setattr(
        "smtplib.SMTP",
        forbidden,
    )

    monkeypatch.setattr(
        "smtplib.SMTP_SSL",
        forbidden,
    )

    message = build_customer_verification_message(
        settings(),
        "customer@example.com",
        "123456",
    )

    assert message["To"] == "customer@example.com"


def test_sender_uses_existing_tls_contract(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(("send", message["To"]))

    monkeypatch.setattr(
        "app.customer_verification_email.smtplib.SMTP",
        FakeSMTP,
    )

    from app.customer_verification_email import (
        send_customer_verification_code,
    )

    config = SimpleNamespace(
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_username="photos@sophiesphotography.co.uk",
        smtp_password="synthetic-password",
        smtp_use_tls=True,
        smtp_from_name="Sophie’s Photography",
        smtp_from_email="photos@sophiesphotography.co.uk",
    )

    send_customer_verification_code(
        config,
        "customer@example.com",
        "123456",
    )

    assert ("starttls",) in calls
    assert (
        "login",
        "photos@sophiesphotography.co.uk",
        "synthetic-password",
    ) in calls
    assert ("send", "customer@example.com") in calls


def test_sender_respects_tls_disabled(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(("send", message["To"]))

    monkeypatch.setattr(
        "app.customer_verification_email.smtplib.SMTP",
        FakeSMTP,
    )

    from app.customer_verification_email import (
        send_customer_verification_code,
    )

    config = SimpleNamespace(
        smtp_host="smtp.example.test",
        smtp_port=25,
        smtp_username="",
        smtp_password="",
        smtp_use_tls=False,
        smtp_from_name="Sophie’s Photography",
        smtp_from_email="photos@sophiesphotography.co.uk",
    )

    send_customer_verification_code(
        config,
        "customer@example.com",
        "654321",
    )

    assert ("starttls",) not in calls
    assert not any(
        call[0] == "login"
        for call in calls
    )
    assert ("send", "customer@example.com") in calls
