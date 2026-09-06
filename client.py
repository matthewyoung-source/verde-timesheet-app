"""Optional end-client approval view. Off by default for every Client
(see Client.requires_client_approval) -- Matthew doesn't use this for Verde's
own clients since he gets verbal approval on a Monday call, but it's built so
this app can be sold to other staffing/construction firms that DO want their
end clients to formally sign off on a week's hours.

A client's approval here is recorded for the record only -- per Matthew's
choice, it does NOT hold up the automatic Sunday-night Xero draft invoice.
"""

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, current_app, send_from_directory
from flask_login import login_required, current_user

from extensions import db
from models import WeeklyPacket, Assignment

client_bp = Blueprint("client", __name__, url_prefix="/client")


def _require_client():
    if not current_user.is_client() or not current_user.client_id:
        abort(403)


@client_bp.route("/")
@login_required
def dashboard():
    _require_client()
    assignment_ids = [
        a.id for a in Assignment.query.filter_by(client_id=current_user.client_id).all()
    ]
    packets = (
        WeeklyPacket.query.filter(WeeklyPacket.assignment_id.in_(assignment_ids))
        .filter(WeeklyPacket.client_approval_status != "not_required")
        .order_by(WeeklyPacket.week_start.desc())
        .limit(20)
        .all()
        if assignment_ids
        else []
    )
    return render_template("client_dashboard.html", packets=packets, client=current_user.client)


@client_bp.route("/packets/<int:packet_id>/download")
@login_required
def download_packet(packet_id):
    _require_client()
    packet = WeeklyPacket.query.get_or_404(packet_id)
    if packet.assignment.client_id != current_user.client_id:
        abort(403)
    if not packet.pdf_filename:
        abort(404)
    return send_from_directory(current_app.config["PDF_FOLDER"], packet.pdf_filename, as_attachment=True)


@client_bp.route("/packets/<int:packet_id>/respond", methods=["POST"])
@login_required
def respond(packet_id):
    _require_client()
    packet = WeeklyPacket.query.get_or_404(packet_id)
    if packet.assignment.client_id != current_user.client_id:
        abort(403)

    decision = request.form.get("decision")
    note = request.form.get("note", "").strip()

    if decision == "approve":
        packet.client_approval_status = "approved"
        packet.client_approved_at = datetime.utcnow()
        packet.client_approval_note = note or None
        flash("Marked as approved. Thanks!", "success")
    elif decision == "dispute":
        packet.client_approval_status = "disputed"
        packet.client_approved_at = datetime.utcnow()
        packet.client_approval_note = note or "Disputed, no reason given."
        flash("Marked as disputed -- Matthew will follow up with you.", "success")
    else:
        flash("Unrecognized response.", "error")
        return redirect(url_for("client.dashboard"))

    db.session.commit()
    return redirect(url_for("client.dashboard"))
