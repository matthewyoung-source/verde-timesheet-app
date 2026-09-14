"""Placement API: the Verde CRM pushes a placement here and this app creates
(or finds) the client, the contractor login and the assignment, then emails
the contractor a link to set their password.

Auth is a shared secret: the CRM sends X-Api-Key equal to PLACEMENT_API_KEY
set on both services. Nothing here is reachable without it.
"""

import hmac
import secrets
from datetime import date

from flask import Blueprint, request, jsonify, current_app

from extensions import db, csrf
from models import User, Client, Assignment
import notifications

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _authorised():
    key = current_app.config.get("PLACEMENT_API_KEY") or ""
    sent = request.headers.get("X-Api-Key", "")
    return bool(key) and hmac.compare_digest(key, sent)


def _money(v):
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _date(v):
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


@api_bp.route("/placements", methods=["POST"])
@csrf.exempt
def create_placement():
    if not _authorised():
        return jsonify({"error": "unauthorised"}), 401
    data = request.get_json(silent=True) or {}
    person = data.get("contractor") or {}
    client_in = data.get("client") or {}
    a = data.get("assignment") or {}

    name = (person.get("name") or "").strip()[:120]
    email = (person.get("email") or "").strip().lower()[:200]
    client_name = (client_in.get("name") or "").strip()[:200]
    billing_rate = _money(a.get("billing_rate"))
    if not name or not email or not client_name or billing_rate is None:
        return jsonify({"error": "contractor name and email, client name and billing_rate are required"}), 400

    client = Client.query.filter(db.func.lower(Client.name) == client_name.lower()).first()
    client_created = False
    if not client:
        client = Client(name=client_name, billing_email=(client_in.get("billing_email") or "").strip()[:200] or None)
        db.session.add(client)
        db.session.flush()
        client_created = True

    user = User.query.filter_by(email=email).first()
    user_created = False
    if not user:
        user = User(name=name, email=email, role="contractor")
        user.set_password(secrets.token_urlsafe(24))  # they set their own via the emailed link
        db.session.add(user)
        db.session.flush()
        user_created = True
    elif user.role != "contractor":
        return jsonify({"error": f"{email} already exists as a {user.role} login"}), 409

    assignment = Assignment(
        contractor_id=user.id, client_id=client.id,
        billing_rate=billing_rate, pay_rate=_money(a.get("pay_rate")),
        per_diem_bill_rate=_money(a.get("per_diem_bill_rate")),
        per_diem_contractor_rate=_money(a.get("per_diem_contractor_rate")),
        per_diem_days=int(a.get("per_diem_days") or 7),
        role_title=(a.get("role_title") or "").strip()[:150] or None,
        start_date=_date(a.get("start_date")) or date.today(),
        invoice_reference_prefix=(a.get("invoice_reference_prefix") or "").strip()[:100] or None,
        po_number=(a.get("po_number") or "").strip()[:100] or None,
    )
    db.session.add(assignment)
    db.session.commit()

    invite_sent = False
    if user_created or data.get("resend_invite"):
        token = user.get_reset_token(current_app.config["SECRET_KEY"])
        link = f"{current_app.config['APP_BASE_URL']}/reset-password/{token}"
        invite_sent = bool(notifications.send_email(
            current_app, user.email, "Your Verde Solutions timesheet login",
            (f"Hi {user.name.split(' ')[0]},\n\n"
             f"Welcome aboard. You will log your hours and receipts for {client.name} in the Verde app.\n"
             f"Set your password here (the link lasts 2 hours; if it has expired, use Forgot password on the login page):\n{link}\n\n"
             f"Then log in at {current_app.config['APP_BASE_URL']} on your phone and add each day's hours before you finish for the day.\n\n"
             "Any questions, reply to this email.\n\nVerde Solutions"),
        ))
    return jsonify({
        "user_id": user.id, "user_created": user_created,
        "client_id": client.id, "client_created": client_created,
        "assignment_id": assignment.id, "invite_sent": invite_sent,
        "assignment_url": f"{current_app.config['APP_BASE_URL']}/admin/contractors/{user.id}",
    }), 201
