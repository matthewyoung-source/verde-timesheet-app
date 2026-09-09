import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _normalized_database_url():
    """Render (and some other hosts) hand out a DATABASE_URL that starts
    with postgres://, but SQLAlchemy 2.x requires postgresql://. Falls back
    to a local SQLite file when no DATABASE_URL is set (e.g. running on
    your own machine)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # Session cookie hardening. SECURE is on whenever the public URL is
    # https (Render), and off for local http runs so login still works.
    SESSION_COOKIE_SECURE = os.environ.get("APP_BASE_URL", "").startswith("https://")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    # Forged-request protection (Flask-WTF). Tokens last the whole session
    # so a contractor who leaves a form open for an hour isn't bounced.
    WTF_CSRF_TIME_LIMIT = None
    # Login attempts per IP before the app makes them wait.
    LOGIN_RATE_LIMIT = os.environ.get("LOGIN_RATE_LIMIT", "10 per minute")
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    # Where "something broke" emails go. Defaults to the first admin's email.
    ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
    SQLALCHEMY_DATABASE_URI = _normalized_database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # On Render these point at the persistent disk (e.g. /var/data/uploads)
    # so photos and PDFs survive redeploys -- set via env vars in render.yaml.
    # Locally they just default to folders next to the app.
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
    PDF_FOLDER = os.environ.get("PDF_FOLDER", os.path.join(BASE_DIR, "generated_pdfs"))
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

    COMPANY_NAME = os.environ.get("COMPANY_NAME", "Verde Solutions")

    # Xero OAuth2 app credentials (from Matthew's own Xero Developer app).
    # Leave blank until connected -- invoice creation is skipped gracefully until then.
    XERO_CLIENT_ID = os.environ.get("XERO_CLIENT_ID", "")
    XERO_CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET", "")
    XERO_REDIRECT_URI = os.environ.get("XERO_REDIRECT_URI", "http://localhost:5000/xero/callback")

    # Outgoing email (password reset links, daily "log your hours" reminders).
    # Leave SMTP_HOST blank until you have a provider -- emails are skipped
    # gracefully (just logged) until then, same as Xero above.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() != "false"
    MAIL_FROM = os.environ.get("MAIL_FROM", "no-reply@verdesolutions.co.uk")

    # Used to build absolute links in emails (password reset, reminders).
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

    # Reminder job: what time each day to nudge contractors who haven't
    # logged anything yet. Pacific time, matches how Matthew runs the business.
    REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "17"))
    REMINDER_MINUTE = int(os.environ.get("REMINDER_MINUTE", "0"))
    # The app's clock. "Today", the current week, the Sunday packet run and
    # every timestamp shown on screen all use this zone, not the server's UTC.
    BUSINESS_TIMEZONE = os.environ.get("BUSINESS_TIMEZONE", "America/Los_Angeles")
    REMINDER_TIMEZONE = os.environ.get("REMINDER_TIMEZONE", BUSINESS_TIMEZONE)
