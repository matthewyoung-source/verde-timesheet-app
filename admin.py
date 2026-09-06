import os
from datetime import datetime

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, current_app, abort, send_from_directory
)
from flask_login import login_required, current_user

from extensions import db
from models import User, Client, Assignment, WeeklyPacket
from utils import week_bounds
import xero_integration

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin():
    if not current_user.is_admin():
        abort(403)


@admin_bp.route("/")
@login_required
def dashboard():
    _require_admin()
    monday, sunday = week_bounds()
    recent_packets = WeeklyPacket.query.order_by(WeeklyPacket.generated_at.desc()).limit(20).all()
    contractors = User.query.filter_by(role="contractor").order_by(User.name).all()
    xero_connected = xero_integration.is_connected()
    return render_template(
        "admin_dashboard.html",
        contractors=contractors,
        recent_packets=recent_packets,
        monday=monday,
        sunday=sunday,
        xero_connected=xero_connected,
    )


@admin_bp.route("/contractors", methods=["GET", "POST"])
@login_required
def contractors():
    _require_admin()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not name or not email or not password:
            flash("Name, email, and a starting password are required.", "error")
        elif User.query.filter_by(email=email).first():
            flash("A user with that email already exists.", "error")
        else:
            user = User(name=name, email=email, role="contractor")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f"Added contractor {name}.", "success")
        return redirect(url_for("admin.contractors"))

    all_contractors = User.query.filter_by(role="contractor").order_by(User.name).all()
    clients = Client.query.filter_by(active=True).order_by(Client.name).all()
    return render_template("admin_contractors.html", contractors=all_contractors, clients=clients)


@admin_bp.route("/clients", methods=["GET", "POST"])
@login_required
def clients():
    _require_admin()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        billing_email = request.form.get("billing_email", "").strip()
        if not name:
            flash("Client name is required.", "error")
        else:
            db.session.add(Client(name=name, billing_email=billing_email or None))
            db.session.commit()
            flash(f"Added client {name}.", "success")
        return redirect(url_for("admin.clients"))

    all_clients = Client.query.order_by(Client.name).all()
    return render_template("admin_clients.html", clients=all_clients)


@admin_bp.route("/assignments", methods=["POST"])
@login_required
def create_assignment():
    _require_admin()
    contractor_id = request.form.get("contractor_id")
    client_id = request.form.get("client_id")
    billing_rate = request.form.get("billing_rate", "").strip()
    role_title = request.form.get("role_title", "").strip()

    try:
        billing_rate_val = float(billing_rate)
    except ValueError:
        flash("Enter a valid billing rate.", "error")
        return redirect(url_for("admin.contractors"))

    assignment = Assignment(
        contractor_id=contractor_id,
        client_id=client_id,
        billing_rate=billing_rate_val,
        role_title=role_title or None,
    )
    db.session.add(assignment)
    db.session.commit()
    flash("Assignment and billing rate saved.", "success")
    return redirect(url_for("admin.contractors"))


@admin_bp.route("/assignments/<int:assignment_id>/rate", methods=["POST"])
@login_required
def update_rate(assignment_id):
    _require_admin()
    assignment = Assignment.query.get_or_404(assignment_id)
    billing_rate = request.form.get("billing_rate", "").strip()
    try:
        assignment.billing_rate = float(billing_rate)
        db.session.commit()
        flash("Billing rate updated.", "success")
    except ValueError:
        flash("Enter a valid billing rate.", "error")
    return redirect(url_for("admin.contractors"))


@admin_bp.route("/packets/<int:packet_id>/download")
@login_required
def download_packet(packet_id):
    _require_admin()
    packet = WeeklyPacket.query.get_or_404(packet_id)
    if not packet.pdf_filename:
        abort(404)
    return send_from_directory(current_app.config["PDF_FOLDER"], packet.pdf_filename, as_attachment=True)


@admin_bp.route("/xero/connect")
@login_required
def xero_connect():
    _require_admin()
    client_id = current_app.config["XERO_CLIENT_ID"]
    redirect_uri = current_app.config["XERO_REDIRECT_URI"]
    if not client_id:
        flash("Add XERO_CLIENT_ID and XERO_CLIENT_SECRET to the app's environment first (see README).", "error")
        return redirect(url_for("admin.dashboard"))
    url = xero_integration.build_authorize_url(client_id, redirect_uri, state="verde")
    return redirect(url)


@admin_bp.route("/xero/callback")
@login_required
def xero_callback():
    _require_admin()
    code = request.args.get("code")
    client_id = current_app.config["XERO_CLIENT_ID"]
    client_secret = current_app.config["XERO_CLIENT_SECRET"]
    redirect_uri = current_app.config["XERO_REDIRECT_URI"]

    if not code:
        flash("Xero did not return an authorization code.", "error")
        return redirect(url_for("admin.dashboard"))

    try:
        xero_integration.exchange_code_for_token(code, client_id, client_secret, redirect_uri)
        flash("Connected to Xero.", "success")
    except Exception as exc:
        flash(f"Could not connect to Xero: {exc}", "error")

    return redirect(url_for("admin.dashboard"))
