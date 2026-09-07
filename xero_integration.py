"""Xero integration: OAuth2 connect flow + draft invoice creation.

Drafts are built from billing.compute_week() lines so they match Verde's real
invoices line for line: Regular Hours, Overtime, Per Diem, then one line per
expense type (all fuel receipts summed into a single Fuel line, etc). Each draft
carries the VerdeCC_<Contractor>_<Site>_<W/E date> reference and the verde2026
branding theme.

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
BRANDING_THEMES_URL = "https://api.xero.com/api.xro/2.0/BrandingThemes"

# Name of the Xero branding theme to stamp on every draft (set up in Xero under
# Invoice settings). Looked up once and cached for the life of the process.
BRANDING_THEME_NAME = "verde2026"
_branding_theme_cache = {}
DUE_DAYS = 30

SCOPES = "openid profile email accounting.contacts accounting.invoices accounting.settings.read offline_access"


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


def _headers(token):
    return {
        "Authorization": f"Bearer {token.access_token}",
        "Xero-tenant-id": token.tenant_id,
        "Accept": "application/json",
    }


def _branding_theme_id(token):
    """ID of the BRANDING_THEME_NAME theme, or None if it doesn't exist yet
    (Xero then falls back to the org default, so the draft still gets created)."""
    key = token.tenant_id
    if key in _branding_theme_cache:
        return _branding_theme_cache[key]
    theme_id = None
    try:
        resp = requests.get(BRANDING_THEMES_URL, headers=_headers(token), timeout=15)
        resp.raise_for_status()
        for theme in resp.json().get("BrandingThemes", []):
            if (theme.get("Name") or "").strip().lower() == BRANDING_THEME_NAME.lower():
                theme_id = theme.get("BrandingThemeID")
                break
    except Exception:
        theme_id = None
    _branding_theme_cache[key] = theme_id
    return theme_id


def _line_items(lines):
    """billing.compute_week() lines -> Xero LineItems. Item codes are only sent
    when the line has one (RH / OT / PD / Fuel exist in Verde's Xero); every
    line always carries its account code so Xero can post it either way."""
    items = []
    for line in lines:
        item = {
            "Description": line["description"],
            "Quantity": float(line["quantity"]),
            "UnitAmount": float(line["unit_amount"]),
            "AccountCode": line.get("account_code") or "4100",
        }
        if line.get("item_code"):
            item["ItemCode"] = line["item_code"]
        items.append(item)
    return items


def create_draft_invoice(client_id, client_secret, client_name, client_xero_contact_id,
                          lines, reference, invoice_date, due_date=None):
    """Creates ONE draft invoice with the given billing lines.
    Returns (invoice_id, status): status is 'draft_created', 'not_connected', or 'failed'."""

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
        due_date = due_date or (invoice_date + timedelta(days=DUE_DAYS))

        payload = {
            "Type": "ACCREC",
            "Contact": contact,
            "Date": invoice_date.isoformat(),
            "DueDate": due_date.isoformat(),
            "Reference": reference,
            "LineAmountTypes": "Exclusive",
            "LineItems": _line_items(lines),
            "Status": "DRAFT",
        }
        theme_id = _branding_theme_id(token)
        if theme_id:
            payload["BrandingThemeID"] = theme_id

        resp = requests.post(
            INVOICES_URL, json={"Invoices": [payload]}, headers=_headers(token), timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        invoice_id = data["Invoices"][0]["InvoiceID"]
        return invoice_id, "draft_created"

    except Exception:
        return None, "failed"


def update_draft_invoice(client_id, client_secret, invoice_id, lines, reference=None):
    """Replaces the lines on an existing DRAFT invoice -- used when an admin
    edits a packet's hours/amounts at approval time, after it has synced.
    If the invoice has since been approved/paid in Xero itself, this call will
    simply fail and the admin keeps their in-app numbers as the source of truth.
    Returns 'updated', 'not_connected', or 'failed'."""

    token = XeroToken.query.first()
    if not token or not client_id or not client_secret or not invoice_id:
        return "not_connected"

    try:
        token = _refresh_if_needed(token, client_id, client_secret)
        payload = {
            "InvoiceID": invoice_id,
            "Type": "ACCREC",
            "LineAmountTypes": "Exclusive",
            "LineItems": _line_items(lines),
        }
        if reference:
            payload["Reference"] = reference
        resp = requests.post(
            f"{INVOICES_URL}/{invoice_id}", json={"Invoices": [payload]},
            headers=_headers(token), timeout=20,
        )
        resp.raise_for_status()
        return "updated"

    except Exception:
        return "failed"


def fetch_invoice_pdf(client_id, client_secret, invoice_id):
    """Returns the invoice as PDF bytes, rendered by Xero with whatever branding
    theme the invoice carries, or None if Xero isn't connected / the call fails."""
    token = XeroToken.query.first()
    if not token or not client_id or not client_secret or not invoice_id:
        return None
    try:
        token = _refresh_if_needed(token, client_id, client_secret)
        headers = _headers(token)
        headers["Accept"] = "application/pdf"
        resp = requests.get(f"{INVOICES_URL}/{invoice_id}", headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None
