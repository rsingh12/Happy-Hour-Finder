"""
Email sending via SendGrid.

Falls back to printing the email to stdout when SENDGRID_API_KEY is empty,
which is the default during local development. This way auth flows work
without any external service in week 1.
"""

import logging

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings


logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str) -> bool:
    if not settings.sendgrid_api_key:
        # Local-dev fallback: print to console so we can see verification codes
        print("\n" + "=" * 60)
        print(f"[DEV EMAIL] To: {to_email}")
        print(f"[DEV EMAIL] Subject: {subject}")
        print(f"[DEV EMAIL] Body:\n{body}")
        print("=" * 60 + "\n")
        return True

    try:
        message = Mail(
            from_email=settings.sendgrid_from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=body,
        )
        client = SendGridAPIClient(settings.sendgrid_api_key)
        response = client.send(message)
        return 200 <= response.status_code < 300
    except Exception as e:
        logger.exception("SendGrid send failed: %s", e)
        return False


def send_verification_code(to_email: str, code: str) -> bool:
    subject = "Your Happy Hour verification code"
    body = (
        f"Welcome to Happy Hour!\n\n"
        f"Your verification code is: {code}\n\n"
        f"This code expires in 15 minutes.\n\n"
        f"If you didn't request this, you can ignore this email."
    )
    return send_email(to_email, subject, body)
