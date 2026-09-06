"""Xero integration: OAuth2 connect flow + draft invoice creation.

This talks to Xero's own Accounting API directly (no third-party
connector needed). To activate it, Matthew needs to:

  1. Go to https://developer.xero.com/app/manage and create a free
     "Xero app" under his own Xero login (this is a one-time step only
     he can do -- it's tied to his Xero organisation).
  2. Set the redirect URI on that app to match XERO_REDIRECT_URI in config.py.
  3. Put the app's Client ID and Client Secret into this app's environment
     as XERO_CLIENT_ID / XERO_CLIENT_SECRET.
  4. Click "Connect to Xero" in the admin panel here and approve access.

Until steps 1-4 are done, every function below is a safe no-op: PDFs
still generate normally, and each WeeklyPacket is marked
xero_invoice_status='not_connected' rather than failing.
"""

from datetime import datetime, timedelta
import requests

from extensions import db
from models import XeroToken

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
INVOICES_URL = "https://api.xero.com/api.xro/2.0/Invoices"

SCOPES = "openid profile email accounting.contacts accounting.transactions offline_access"


def is_connected():
    return XeroToken.query.first() is not None


def build_authorize_url(client_id, redirect_uri, state):
    return (
        f"{AUTHORIZE_URL}?response_type=code&client_id={client_id}"
        f"&redirect_uri={redirect_uri}&scope={SCOPES.replace(' ', '%20')}&state={state}"
    )


def exchange_code_for_token(code, client_id, client_secret, redirect_uri):
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    token_data = resp.json()

    conn_resp = requests.get(
        CONNECTIONS_URL,
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
        timeout=15,
    )
    conn_resp.raise_for_status()
    connections = conn_resp.json()
    tenant_id = connections[0]["tenantId"] if connections else None

    existing = XeroToken.query.first()
    if existing:
        db.session.delete(existing)
        db.session.commit()

    token = XeroToken(
        access_token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        expires_at=datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 1800)),
        tenant_id=tenant_id,
    )
    db.session.add(token)
    db.session.commit()
    return token


def _refresh_if_needed(token, client_id, client_secret):
    if datetime.utcnow() < token.expires_at - timedelta(minutes=2):
        return token

    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": token.refresh_token},
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token.access_token = data["access_token"]
    token.refresh_token = data["refresh_token"]
    token.expires_at = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 1800))
    db.session.commit()
    return token


def create_draft_invoice(client_id, client_secret, client_name, client_xero_contact_id,
                          contractor_name, week_start, week_end, hours, billing_rate):
    """Creates ONE draft invoice line for hours worked at billing_rate.
    Expenses are deliberately excluded, per how Matthew invoices clients.
    Returns (invoice_id, status) -- status is 'draft_created', 'not_connected', or 'failed'."""

    token = XeroToken.query.first()
    if not token or not client_id or not client_secret:
        return None, "not_connected"

    try:
        token = _refresh_if_needed(token, client_id, client_secret)

        contact = (
            {"ContactID": client_xero_contact_id}
            if client_xero_contact_id
            else {"Name": client_name}
        )

        description = (
            f"{contractor_name} - hours worked "
            f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}"
        )

        payload = {
            "Type": "ACCREC",
            "Contact": contact,
            "LineItems": [
                {
                    "Description": description,
                    "Quantity": float(hours),
                    "UnitAmount": float(billing_rate),
                    "AccountCode": "200",  # default sales account; adjust in Xero if needed
                }
            ],
            "Status": "DRAFT",
        }

        resp = requests.post(
            INVOICES_URL,
            json={"Invoices": [payload]},
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "Xero-tenant-id": token.tenant_id,
                "Accept": "application/json",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        invoice_id = data["Invoices"][0]["InvoiceID"]
        return invoice_id, "draft_created"

    except Exception:
        return None, "failed"
