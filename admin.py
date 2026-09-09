import os
import secrets
from datetime import datetime, date, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, current_app, abort,
    send_from_directory, send_file,
)
from flask_login import login_required, current_user
from sqlalchemy import func

from extensions import db
from models import User, Client, Assignment, WeeklyPacket, TimesheetEntry, Expense, WeekSubmission, JobRun
from utils import week_bounds, business_today
import billing
import packets
import xero_integration

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin():
    if not current_user.is_admin():
        abort(403)


def _generate_temp_password():
    # Words-and-digits so Matthew can read it aloud/text it easily, e.g. "amber-plank-8341".
    words = ["amber", "cedar", "plank", "ridge", "delta", "birch", "quartz", "meadow", "harbor", "cinder"]
    return f"{secrets.choice(words)}-{secrets.choice(words)}-{secrets.randbelow(9000) + 1000}"


@admin_bp.route("/run-packets", methods=["POST"])
@login_required
def run_packets_now():
    """Build this week's packets right now instead of waiting for Sunday
    night. Safe to press twice: weeks that already have a packet are skipped."""
    _require_admin()
    from scheduler_jobs import run_packets
    result = run_packets(current_app._get_current_object(), source="manual")
    flash(("Packets built. " if result.ok else "Packet run had problems: ") + result.message, "success" if result.ok else "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/")
@login_required
def dashboard():
    _require_admin()
    today = business_today()
    monday, sunday = week_bounds(today)
    recent_packets = WeeklyPacket.query.order_by(WeeklyPacket.week_start.desc(), WeeklyPacket.id.desc()).limit(8).all()
    pending_packets = (
        WeeklyPacket.query.filter_by(approval_status="pending")
        .order_by(WeeklyPacket.week_start.desc()).all()
    )
    contractors = User.query.filter_by(role="contractor").order_by(User.name).all()
    xero_connected = xero_integration.is_connected()
    last_packets = JobRun.latest_finished("weekly_packets")
    last_reminder = JobRun.latest_finished("daily_reminder")

    # Who has logged what this week, one row per active assignment.
    days = [monday + timedelta(days=i) for i in range(7)]
    submitted = {
        s.assignment_id: s for s in WeekSubmission.query.filter_by(week_start=monday).all()
    }
    week_rows = []
    for a in Assignment.query.filter_by(active=True).all():
        entries = a.timesheet_entries.filter(
            TimesheetEntry.work_date >= monday, TimesheetEntry.work_date <= sunday
        ).all()
        by_day = {e.work_date: float(e.hours or 0) for e in entries}
        expenses = a.expenses.filter(
            Expense.expense_date >= monday, Expense.expense_date <= sunday
        ).all()
        week_rows.append({
            "assignment": a,
            "hours": round(sum(by_day.values()), 2),
            "days": [(d, by_day.get(d)) for d in days],
            "expense_count": len(expenses),
            "expense_total": round(sum(float(x.amount or 0) for x in expenses), 2),
            "unconfirmed": sum(1 for x in expenses if not x.is_amount_confirmed),
            "submitted": submitted.get(a.id),
            "last_logged": max((e.work_date for e in entries), default=None),
        })
    week_rows.sort(key=lambda r: (r["assignment"].contractor.name, r["assignment"].client.name))
    expenses_this_week = round(sum(r["expense_total"] for r in week_rows), 2)

    active_contractor_count = User.query.filter_by(role="contractor", active=True).count()
    active_assignment_count = Assignment.query.filter_by(active=True).count()
    pending_approval_count = WeeklyPacket.query.filter_by(approval_status="pending").count()
    hours_this_week = (
        db.session.query(func.coalesce(func.sum(TimesheetEntry.hours), 0))
        .filter(TimesheetEntry.work_date >= monday, TimesheetEntry.work_date <= sunday)
        .scalar()
    )

    return render_template(
        "admin_dashboard.html",
        last_packets=last_packets, last_reminder=last_reminder,
        contractors=contractors,
        recent_packets=recent_packets,
        pending_packets=pending_packets,
        week_rows=week_rows,
        expenses_this_week=expenses_this_week,
        today=today,
        monday=monday,
        sunday=sunday,
        xero_connected=xero_connected,
        active_contractor_count=active_contractor_count,
        active_assignment_count=active_assignment_count,
        pending_approval_count=pending_approval_count,
        hours_this_week=hours_this_week,
    )


@admin_bp.route("/packets")
@login_required
def packets_list():
    """Every weekly packet, newest first, filterable by approval status."""
    _require_admin()
    status = request.args.get("status", "all")
    contractor_id = request.args.get("contractor", type=int)
    q = WeeklyPacket.query.join(Assignment, WeeklyPacket.assignment_id == Assignment.id)
    if status == "pending":
        q = q.filter(WeeklyPacket.approval_status == "pending")
    elif status == "approved":
        q = q.filter(WeeklyPacket.approval_status == "approved")
    elif status == "xero_failed":
        q = q.filter(WeeklyPacket.xero_invoice_status == "failed")
    if contractor_id:
        q = q.filter(Assignment.contractor_id == contractor_id)
    rows = q.order_by(WeeklyPacket.week_start.desc(), WeeklyPacket.id.desc()).limit(200).all()

    counts = {
        "all": WeeklyPacket.query.count(),
        "pending": WeeklyPacket.query.filter_by(approval_status="pending").count(),
        "approved": WeeklyPacket.query.filter_by(approval_status="approved").count(),
        "xero_failed": WeeklyPacket.query.filter_by(xero_invoice_status="failed").count(),
    }
    contractors = User.query.filter_by(role="contractor").order_by(User.name).all()
    return render_template(
        "admin_packets.html", packets=rows, status=status, counts=counts,
        contractors=contractors, selected_contractor=contractor_id,
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
    _apply_billing_fields(assignment, request.form)
    db.session.add(assignment)
    db.session.commit()
    flash("Assignment and billing details saved.", "success")
    return redirect(url_for("admin.contractors"))


def _money_or_none(raw):
    raw = (raw or "").strip()
    if raw == "":
        return None
    return float(raw)


def _apply_billing_fields(assignment, form):
    """Reads the invoicing fields shared by the add and edit assignment forms."""
    try:
        assignment.overtime_rate = _money_or_none(form.get("overtime_rate"))
        assignment.per_diem_bill_rate = _money_or_none(form.get("per_diem_bill_rate"))
        assignment.per_diem_contractor_rate = _money_or_none(form.get("per_diem_contractor_rate"))
        days = (form.get("per_diem_days") or "").strip()
        assignment.per_diem_days = int(days) if days != "" else 7
        brk = (form.get("daily_break_hours") or "").strip()
        assignment.daily_break_hours = float(brk) if brk != "" else 0
    except ValueError:
        pass  # leave whatever was there; the field-level checks on the form stop most of this
    assignment.po_number = (form.get("po_number") or "").strip() or None
    assignment.invoice_reference_prefix = (form.get("invoice_reference_prefix") or "").strip() or None


@admin_bp.route("/assignments/<int:assignment_id>/billing", methods=["POST"])
@login_required
def update_billing(assignment_id):
    _require_admin()
    assignment = Assignment.query.get_or_404(assignment_id)
    billing_rate = request.form.get("billing_rate", "").strip()
    try:
        if billing_rate:
            assignment.billing_rate = float(billing_rate)
    except ValueError:
        flash("Enter a valid billing rate.", "error")
        return redirect(url_for("admin.contractors"))
    _apply_billing_fields(assignment, request.form)
    db.session.commit()
    flash(f"Billing details updated for {assignment.contractor.name} at {assignment.client.name}.", "success")
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
    assignment.end_date = business_today()
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
            start_raw = request.form.get(f"start_{entry.id}", "").strip()
            end_raw = request.form.get(f"end_{entry.id}", "").strip()
            hours_raw = request.form.get(f"hours_{entry.id}", "").strip()
            start_t = _parse_time(start_raw)
            end_t = _parse_time(end_raw)
            if start_t and end_t:
                entry.start_time = start_t
                entry.end_time = end_t
                entry.hours = billing.hours_from_times(start_t, end_t, assignment.daily_break_hours)
            elif hours_raw:
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
            category = request.form.get(f"category_{exp.id}", "").strip()
            if category:
                exp.category = category

        per_diem_raw = request.form.get("per_diem_days", "").strip()
        per_diem_days = None
        if per_diem_raw != "":
            try:
                per_diem_days = max(0, min(7, int(per_diem_raw)))
            except ValueError:
                per_diem_days = None

        packets.regenerate_packet(packet, entries, packet_expenses, per_diem_days=per_diem_days)

        if action == "approve":
            packet.approval_status = "approved"
            packet.approved_at = datetime.utcnow()
            db.session.commit()
            flash("Packet approved and locked -- the contractor can no longer edit this week.", "success")
        else:
            db.session.commit()
            flash("Packet updated and PDF regenerated.", "success")

        return redirect(url_for("admin.packet_detail", packet_id=packet.id))

    figures = billing.compute_week(assignment, entries, packet_expenses, packet.per_diem_days)

    return render_template(
        "admin_packet_detail.html",
        packet=packet,
        assignment=assignment,
        entries=entries,
        expenses=packet_expenses,
        figures=figures,
        categories=list(billing.EXPENSE_CATEGORIES.keys()),
    )


def _parse_time(raw):
    """'08:00' from an <input type=time> -> datetime.time, else None."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


@admin_bp.route("/packets/<int:packet_id>/regenerate", methods=["POST"])
@login_required
def regenerate_packet_now(packet_id):
    """Rebuild a packet's PDF (and Xero draft) without changing any figures --
    handy after updating a logo or assignment billing details."""
    _require_admin()
    packet = WeeklyPacket.query.get_or_404(packet_id)
    entries, expenses = packets.week_rows(packet.assignment, packet.week_start, packet.week_end)
    packets.regenerate_packet(packet, entries, expenses, per_diem_days=packet.per_diem_days)
    db.session.commit()
    flash("PDF regenerated.", "success")
    return redirect(url_for("admin.packet_detail", packet_id=packet.id))


@admin_bp.route("/assignments/<int:assignment_id>/generate-now", methods=["POST"])
@login_required
def generate_packet_now(assignment_id):
    """Build this week's packet on demand instead of waiting for Sunday night."""
    _require_admin()
    assignment = Assignment.query.get_or_404(assignment_id)
    monday, sunday = week_bounds()
    existing = WeeklyPacket.query.filter_by(assignment_id=assignment.id, week_start=monday).first()
    if existing:
        flash("This week's packet already exists -- open it to regenerate.", "error")
        return redirect(url_for("admin.packet_detail", packet_id=existing.id))
    entries, expenses = packets.week_rows(assignment, monday, sunday)
    if not entries and not expenses:
        flash("Nothing logged this week yet for that assignment.", "error")
        return redirect(url_for("admin.contractors"))
    packet = packets.create_packet(assignment, monday, sunday)
    db.session.commit()
    flash("Packet generated.", "success")
    return redirect(url_for("admin.packet_detail", packet_id=packet.id))


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


@admin_bp.route("/packets/<int:packet_id>/download-full")
@login_required
def download_full_packet(packet_id):
    """Invoice + timesheet + expenses + receipts as one PDF, ready to send."""
    _require_admin()
    packet = WeeklyPacket.query.get_or_404(packet_id)
    merged, invoice_included = packets.full_packet_pdf(packet)
    if merged is None:
        abort(404)
    if not invoice_included:
        flash("Xero invoice could not be fetched, so this download is the timesheet packet only.", "error")
    name = packet.pdf_filename.replace("timesheet_", "invoice_and_timesheet_", 1)
    return send_file(merged, mimetype="application/pdf", as_attachment=True, download_name=name)


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
