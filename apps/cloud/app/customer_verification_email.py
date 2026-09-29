from __future__ import annotations

import smtplib
from email.message import EmailMessage


def build_customer_verification_message(
    settings,
    email: str,
    code: str,
) -> EmailMessage:
    message = EmailMessage()

    from_name = (
        getattr(settings, "smtp_from_name", "")
        or "Sophie’s Photography"
    )

    from_email = (
        getattr(settings, "smtp_from_email", "")
        or getattr(settings, "smtp_username", "")
    )

    message["Subject"] = (
        f"{code} is your Sophie’s Photography verification code"
    )
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = email

    message.set_content(
        "\n".join(
            [
                "Sophie’s Photography",
                "",
                "Your verification code is:",
                "",
                code,
                "",
                "This code expires in 10 minutes.",
                "",
                "If you did not request this code, "
                "you can ignore this email.",
                "",
                "Sophie’s Photography",
            ]
        )
    )

    message.add_alternative(
        f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f3f3f3;">
  <div style="max-width:560px;margin:0 auto;padding:28px 16px;">
    <div style="background:#111;color:#fff;border-radius:16px;padding:30px;">
      <div style="font-size:13px;font-weight:700;letter-spacing:2px;">
        SOPHIE’S PHOTOGRAPHY
      </div>

      <h1 style="font-size:24px;margin:18px 0 8px;">
        Verify your email
      </h1>

      <p style="line-height:1.5;">
        Enter this six-digit code to continue:
      </p>

      <div style="
        margin:24px 0;
        padding:18px;
        background:#fff;
        color:#111;
        border-radius:10px;
        text-align:center;
        font-size:32px;
        font-weight:800;
        letter-spacing:8px;
      ">{code}</div>

      <p style="line-height:1.5;">
        This code expires in 10 minutes.
      </p>

      <p style="font-size:13px;opacity:.75;line-height:1.5;">
        If you did not request this code, you can ignore this email.
      </p>
    </div>
  </div>
</body>
</html>
""",
        subtype="html",
    )

    return message


def send_customer_verification_code(
    settings,
    email: str,
    code: str,
) -> None:
    message = build_customer_verification_message(
        settings,
        email,
        code,
    )

    host = settings.smtp_host
    port = int(settings.smtp_port)
    username = settings.smtp_username
    password = settings.smtp_password

    with smtplib.SMTP(
        host,
        port,
        timeout=20,
    ) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()

        if username:
            smtp.login(username, password)

        smtp.send_message(message)
