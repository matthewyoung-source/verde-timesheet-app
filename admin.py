import os
import secrets
from datetime import datetime, date, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, current_app, abort, send_from_directory
)
from flask_login import login_required, current_user
from sqlalchemy import func

from extensions import db
from models import User, Client, Assignment, WeeklyPacket, TimesheetEntry, Expense
from utils import week_bounds
from pdf_generator import build_weekly_pdf
import xero_integration

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin():
    if not current_user.is_admin():
        abort(403)


def _generate_temp_password():
    # Words-and-digits so Matthew can read it aloud/text it easily, e.g. "amber-plank-8341".
    words = ["amber", "cedar", "plank", "ridge", "delta", "birch", "quartz", "meadow", "harbor", "cinder"]
    return f"{secrets.choice(words)}-{secrets.choice(words)}-{secrets.randbelow(9000) + 1000}"


def _regenerate_packet(packet, assignment, entries, expenses):
    """Rebuilds a packet's PDF (same filename, in place) and refreshes its
    total_hours/total_expenses from whatever's in `entries`/`expenses` now --
    used when an admin edits hours/amounts before or at approval time."""
    expense_rows = []
    for exp in expenses:
        photo_path = os.path.join(current_app.config["UPLOAD_FOLDER"], exp.photo_filename)
        expense_rows.append(
            type("ExpenseRow", (), {
                "expense_date": exp.expense_date,
                "amount": exp.amount,
                "description": exp.description,
                "photo_path": photo_path,
            })
        )

    output_path = os.path.join(current_app.config["PDF_FOLDER"], packet.pdf_filename)
    totals = build_weekly_pdf(
        output_path=output_path,
        company_name=current_app.config["COMPANY_NAME"],
        contractor_name=assignment.contractor.name,
        client_name=assignment.client.name,
        role_title=assignment.role_title,
        week_start=packet.week_start,
        week_end=packet.week_end,
        billing_rate=float(assignment.billing_rate),
        timesheet_entries=entries,
        expenses=expense_rows,
    )
    packet.total_hours = totals["total_hours"]
    packet.total_expenses = totals["total_expenses"]


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


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
def reset_password(user_id):
    _require_admin()
    user = User.query.get_or_404(user_id)
    temp_password = _generate_temp_password()
    user.set_password(temp_password)
    db.session.commit()
    flash(f"New temporary password for {user.name}: {temp_password} (share this with them directly -- it won't be shown again).", "success")
    return redirect(request.referrer or url_for("admin.dashboard"))


@admin_bp.route("/assignments/<int:assignment_id>/end", methods=["POST"])
@login_required
def end_assignment(assignment_id):
    _require_admin()
    assignment = Assignment.query.get_or_404(assignment_id)
    assignment.active = False
    assignment.end_date = date.today()
    db.session.commit()
    flash(f"Ended {assignment.contractor.name}'s assignment with {assignment.client.name}.", "success")
    return redirect(url_for("admin.contractors"))


@admin_bp.route("/clients/<int:client_id>/toggle-approval", methods=["POST"])
@login_required
def toggle_client_approval(client_id):
    _require_admin()
    client = Client.query.get_or_404(client_id)
    client.requires_client_approval = not client.requires_client_approval
    db.session.commit()
    flash(
        f"Client sign-off {'enabled' if client.requires_client_approval else 'disabled'} for {client.name}.",
        "success",
    )
    return redirect(url_for("admin.clients"))


@admin_bp.route("/clients/<int:client_id>/contacts", methods=["POST"])
@login_required
def create_client_contact(client_id):
    _require_admin()
    client = Client.query.get_or_404(client_id)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not name or not email:
        flash("Name and email are required for a client contact login.", "error")
    elif User.query.filter_by(email=email).first():
        flash("A user with that email already exists.", "error")
    else:
        temp_password = _generate_temp_password()
        contact = User(name=name, email=email, role="client", client_id=client.id)
        contact.set_password(temp_password)
        db.session.add(contact)
        db.session.commit()
        flash(f"Added client contact {name}. Temporary password: {temp_password}", "success")

    return redirect(url_for("admin.clients"))


@admin_bp.route("/packets/<int:packet_id>", methods=["GET", "POST"])
@login_required
def packet_detail(packet_id):
    _require_admin()
    packet = WeeklyPacket.query.get_or_404(packet_id)
    assignment = packet.assignment

    entries = assignment.timesheet_entries.filter(
        TimesheetEntry.work_date >= packet.week_start, TimesheetEntry.work_date <= packet.week_end
    ).order_by(TimesheetEntry.work_date).all()
    packet_expenses = assignment.expenses.filter(
        Expense.expense_date >= packet.week_start, Expense.expense_date <= packet.week_end
    ).order_by(Expense.expense_date).all()

    if request.method == "POST":
        action = request.form.get("action")

        for entry in entries:
            hours_raw = request.form.get(f"hours_{entry.id}", "").strip()
            if hours_raw:
                try:
                    entry.hours = float(hours_raw)
                except ValueError:
                    pass
        for exp in packet_expenses:
            amount_raw = request.form.get(f"amount_{exp.id}", "").strip()
            if amount_raw:
                try:
                    exp.amount = float(amount_raw)
                except ValueError:
                    pass

        _regenerate_packet(packet, assignment, entries, packet_expenses)

        if action == "approve":
            packet.approval_status = "approved"
            packet.approved_at = datetime.utcnow()
            db.session.commit()

            if packet.xero_invoice_id:
                status = xero_integration.update_draft_invoice_hours(
                    client_id=current_app.config["XERO_CLIENT_ID"],
                    client_secret=current_app.config["XERO_CLIENT_SECRET"],
                    invoice_id=packet.xero_invoice_id,
                    contractor_name=assignment.contractor.name,
                    week_start=packet.week_start,
                    week_end=packet.week_end,
                    hours=packet.total_hours,
                    billing_rate=float(assignment.billing_rate),
                )
                if status == "updated":
                    packet.xero_invoice_status = "draft_created"
                db.session.commit()

            flash("Packet approved and locked -- the contractor can no longer edit this week.", "success")
        else:
            db.session.commit()
            flash("Packet updated.", "success")

        return redirect(url_for("admin.packet_detail", packet_id=packet.id))

    return render_template(
        "admin_packet_detail.html",
        packet=packet,
        assignment=assignment,
        entries=entries,
        expenses=packet_expenses,
    )


@admin_bp.route("/reports")
@login_required
def reports():
    _require_admin()

    client_stats = (
        db.session.query(
            Client.name,
            func.coalesce(func.sum(WeeklyPacket.total_hours), 0),
            func.coalesce(func.sum(WeeklyPacket.total_expenses), 0),
            func.coalesce(func.sum(WeeklyPacket.total_hours * Assignment.billing_rate), 0),
        )
        .join(Assignment, Assignment.client_id == Client.id)
        .join(WeeklyPacket, WeeklyPacket.assignment_id == Assignment.id)
        .group_by(Client.name)
        .order_by(Client.name)
        .all()
    )

    # "Billed" = hours x the client billing rate. There's no separate
    # contractor pay-rate field in this app, so this is what clients are
    # billed for the contractor's time, not what the contractor is paid.
    contractor_stats = (
        db.session.query(
            User.name,
            func.coalesce(func.sum(WeeklyPacket.total_hours), 0),
            func.coalesce(func.sum(WeeklyPacket.total_hours * Assignment.billing_rate), 0),
        )
        .join(Assignment, Assignment.contractor_id == User.id)
        .join(WeeklyPacket, WeeklyPacket.assignment_id == Assignment.id)
        .group_by(User.name)
        .order_by(User.name)
        .all()
    )

    status_counts = dict(
        db.session.query(WeeklyPacket.approval_status, func.count(WeeklyPacket.id))
        .group_by(WeeklyPacket.approval_status)
        .all()
    )
    client_approval_counts = dict(
        db.session.query(WeeklyPacket.client_approval_status, func.count(WeeklyPacket.id))
        .group_by(WeeklyPacket.client_approval_status)
        .all()
    )

    pending_packets = (
        WeeklyPacket.query.filter_by(approval_status="pending")
        .order_by(WeeklyPacket.week_start.desc())
        .all()
    )

    return render_template(
        "admin_reports.html",
        client_stats=client_stats,
        contractor_stats=contractor_stats,
        status_counts=status_counts,
        client_approval_counts=client_approval_counts,
        pending_packets=pending_packets,
    )


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
