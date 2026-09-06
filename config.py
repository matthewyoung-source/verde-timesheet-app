import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    PDF_FOLDER = os.path.join(BASE_DIR, "generated_pdfs")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

    COMPANY_NAME = os.environ.get("COMPANY_NAME", "Verde Solutions")

    # Xero OAuth2 app credentials (from Matthew's own Xero Developer app).
    # Leave blank until connected -- invoice creation is skipped gracefully until then.
    XERO_CLIENT_ID = os.environ.get("XERO_CLIENT_ID", "")
    XERO_CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET", "")
    XERO_REDIRECT_URI = os.environ.get("XERO_REDIRECT_URI", "http://localhost:5000/xero/callback")
