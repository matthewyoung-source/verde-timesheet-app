"""Outgoing email: password reset links and daily "log your hours"
reminders. Talks to a plain SMTP server (works with Gmail/Office 365 app
passwords, or a transactional provider like SendGrid/Postmark's SMTP
relay -- no vendor-specific API needed).

Until SMTP_HOST is set in the environment, send_email() is a safe no-op:
it just logs what would have been sent. This mirrors how xero_integration.py
behaves before Xero is connected, so nothing breaks if email isn't set up yet.
"""

import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("notifications")


def is_configured(app):
    return bool(app.config.get("SMTP_HOST"))


def send_email(app, to_address, subject, body_text):
    """Returns True if the email was actually sent, False if it was only logged
    (because SMTP isn't configured yet) or if sending failed."""
    if not is_configured(app):
        logger.info("[email not configured -- would send] to=%s subject=%r", to_address, subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = app.config["MAIL_FROM"]
    msg["To"] = to_address
    msg.set_content(body_text)

    try:
        with smtplib.SMTP(app.config["SMTP_HOST"], app.config["SMTP_PORT"], timeout=15) as server:
            if app.config.get("SMTP_USE_TLS", True):
                server.starttls()
            if app.config.get("SMTP_USERNAME"):
                server.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_address)
        return False
