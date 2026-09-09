"""Contractor side of the app: phone-first screens for logging a day's hours,
adding receipts, submitting the week, and looking back at past weeks.

Editing rules (kept deliberately simple for the field):
  * The current week is always editable.
  * Last week stays editable until Matthew approves and locks it, so a
    Sunday shift logged on Monday morning still lands in the right packet.
    If that week's packet already exists, saving rebuilds the PDF (and the
    Xero draft) so nothing drifts out of sync.
  * Older weeks are read-only.
"""

import os
import uuid
from datetime import datetime, date, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, current_app, abort
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps

try:  # iPhone photos arrive as HEIC; this teaches Pillow to open them.
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

from extensions import db
from models import Assignment, TimesheetEntry, Expense, WeeklyPacket, WeekSubmission
from ocr import extract_amount_from_receipt
from utils import week_bounds, business_today
import billing
import packets

contractor_bp = Blueprint("contractor", __name__, url_prefix="/my")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "heic", "heif", "webp"}
MAX_PHOTO_EDGE = 2000  # px: plenty for a legible receipt, keeps PDFs small
HISTORY_WEEKS = 12


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _normalise_photo(raw_path, jpeg_path):
    """Turns whatever the phone sent (HEIC, huge PNG, sideways JPEG) into an
    upright, reasonably sized JPEG that browsers, OCR and the PDF can all
    read. Returns True on success; on failure the original is kept as is."""
    try:
        with Image.open(raw_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE))
            im.save(jpeg_path, "JPEG", quality=88, optimize=True)
        if raw_path != jpeg_path:
            os.remove(raw_path)
        return True
    except Exception:
        return False


def _require_contractor():
    if current_user.is_admin() or current_user.is_client():
        abort(403)


def _own_assignment(assignment_id):
    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.contractor_id != current_user.id:
        abort(403)
    return assignment


def _parse_time(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


def _parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        abort(404)


def week_state(assignment, monday, today=None):
    """Everything a screen needs to know about one assignment for one week."""
    today = today or business_today()
    sunday = monday + timedelta(days=6)
    this_monday, _ = week_bounds(today)
    days = [monday + timedelta(days=i) for i in range(7)]

    entries = {
        e.work_date: e
        for e in assignment.timesheet_entries.filter(
            TimesheetEntry.work_date >= monday, TimesheetEntry.work_date <= sunday
        ).all()
    }
    expenses = assignment.expenses.filter(
        Expense.expense_date >= monday, Expense.expense_date <= sunday
    ).order_by(Expense.expense_date.desc(), Expense.submitted_at.desc()).all()
    packet = WeeklyPacket.query.filter_by(assignment_id=assignment.id, week_start=monday).first()
    submission = WeekSubmission.query.filter_by(assignment_id=assignment.id, week_start=monday).first()

    locked = bool(packet and packet.is_locked())
    is_current = monday == this_monday
    is_last = monday == this_monday - timedelta(days=7)
    editable = (is_current or is_last) and not locked

    hours = round(sum(float(e.hours or 0) for e in entries.values()), 2)
    expense_total = round(sum(float(x.amount or 0) for x in expenses), 2)
    unconfirmed = sum(1 for x in expenses if not x.is_amount_confirmed)

    if locked:
        status, status_class = "Approved & locked", "green"
    elif submission:
        status, status_class = "Submitted", "brand"
    elif entries or expenses:
        status, status_class = "In progress", "warning"
    else:
        status, status_class = "Nothing logged", "grey"

    return {
        "assignment": assignment,
        "monday": monday,
        "sunday": sunday,
        "days": days,
        "entries": entries,
        "expenses": expenses,
        "expense_total": expense_total,
        "unconfirmed": unconfirmed,
        "hours": hours,
        "regular_hours": min(hours, billing.REGULAR_HOURS_CAP),
        "overtime_hours": round(max(hours - billing.REGULAR_HOURS_CAP, 0), 2),
        "days_logged": len(entries),
        "packet": packet,
        "locked": locked,
        "submitted": submission,
        "editable": editable,
        "is_current": is_current,
        "is_last": is_last,
        "status": status,
        "status_class": status_class,
        "today": today,
    }


def _sync_packet(assignment, monday):
    """If a packet already exists for this week (last week, generated Sunday
    night) rebuild it so the PDF and Xero draft match what was just saved."""
    packet = WeeklyPacket.query.filter_by(assignment_id=assignment.id, week_start=monday).first()
    if not packet or packet.is_locked():
        return
    entries, expenses = packets.week_rows(assignment, packet.week_start, packet.week_end)
    try:
        packets.regenerate_packet(packet, entries, expenses, per_diem_days=packet.per_diem_days)
    except Exception:
        current_app.logger.exception("Could not regenerate packet %s after contractor edit", packet.id)


def _active_assignments():
    return current_user.assignments.filter_by(active=True).order_by(Assignment.id).all()


def _back_to_week(assignment, monday):
    if monday == week_bounds()[0]:
        return url_for("contractor.dashboard")
    return url_for("contractor.week", assignment_id=assignment.id, monday_str=monday.isoformat())


# --------------------------------------------------------------------------
# This week
# --------------------------------------------------------------------------

@contractor_bp.route("/")
@login_required
def dashboard():
    _require_contractor()
    today = business_today()
    monday, _ = week_bounds(today)
    assignments = _active_assignments()
    weeks = [week_state(a, monday, today) for a in assignments]

    # Last week still open? Surface a nudge early in the week so Sunday
    # hours and any last receipts get logged before Matthew's Monday call.
    last_monday = monday - timedelta(days=7)
    last_weeks = []
    for a in assignments:
        w = week_state(a, last_monday, today)
        if w["editable"] and not w["submitted"] and (w["entries"] or w["expenses"]):
            last_weeks.append(w)

    return render_template(
        "contractor_dashboard.html", weeks=weeks, last_weeks=last_weeks,
        monday=monday, sunday=monday + timedelta(days=6), today=today,
    )


@contractor_bp.route("/day/<int:assignment_id>/<day_str>", methods=["GET", "POST"])
@login_required
def day(assignment_id, day_str):
    """One day's hours: start and end (hours work themselves out) or a plain figure."""
    _require_contractor()
    assignment = _own_assignment(assignment_id)
    work_date = _parse_date(day_str)
    monday, _ = week_bounds(work_date)
    state = week_state(assignment, monday)
    entry = state["entries"].get(work_date)

    if request.method == "POST":
        if not state["editable"]:
            flash("That week is locked. Ask your contact at Verde if something needs to change.", "error")
            return redirect(_back_to_week(assignment, monday))

        if request.form.get("action") == "clear":
            if entry:
                db.session.delete(entry)
                db.session.commit()
                _sync_packet(assignment, monday)
                db.session.commit()
            flash(f"{work_date.strftime('%A')} cleared.", "success")
            return redirect(_back_to_week(assignment, monday))

        start_t = _parse_time(request.form.get("start_time"))
        end_t = _parse_time(request.form.get("end_time"))
        hours_raw = (request.form.get("hours") or "").strip()
        notes = (request.form.get("notes") or "").strip()[:300]

        hours = None
        if start_t and end_t:
            hours = billing.hours_from_times(start_t, end_t, assignment.daily_break_hours)
        elif hours_raw:
            try:
                hours = float(hours_raw)
            except ValueError:
                hours = None
            start_t = end_t = None

        if hours is None or hours <= 0:
            flash("Enter a start and end time, or the number of hours.", "error")
            return redirect(url_for("contractor.day", assignment_id=assignment.id, day_str=work_date.isoformat()))
        if hours > 24:
            flash("That's more than 24 hours in a day. Double check the times.", "error")
            return redirect(url_for("contractor.day", assignment_id=assignment.id, day_str=work_date.isoformat()))

        if entry:
            entry.hours, entry.start_time, entry.end_time, entry.notes = hours, start_t, end_t, notes
        else:
            db.session.add(TimesheetEntry(
                assignment_id=assignment.id, work_date=work_date, hours=hours,
                start_time=start_t, end_time=end_t, notes=notes,
            ))
        db.session.commit()
        _sync_packet(assignment, monday)
        db.session.commit()
        flash(f"{work_date.strftime('%A')} saved: {hours:g} hrs.", "success")
        return redirect(_back_to_week(assignment, monday))

    # Default the clock times to the contractor's most recent shift so a
    # normal day is one tap: open, glance, save.
    recent = None
    if not entry:
        recent = (
            assignment.timesheet_entries.filter(TimesheetEntry.start_time.isnot(None))
            .order_by(TimesheetEntry.work_date.desc()).first()
        )

    prev_day = work_date - timedelta(days=1)
    next_day = work_date + timedelta(days=1)
    this_monday, _ = week_bounds()
    return render_template(
        "contractor_day.html", assignment=assignment, work_date=work_date, entry=entry,
        state=state, prev_day=prev_day, next_day=next_day,
        prev_ok=(prev_day >= this_monday - timedelta(days=7)),
        next_ok=(next_day <= this_monday + timedelta(days=6)),
        break_hours=float(assignment.daily_break_hours or 0),
        back_url=_back_to_week(assignment, monday), recent=recent,
    )


@contractor_bp.route("/submit/<int:assignment_id>/<monday_str>", methods=["POST"])
@login_required
def submit_week(assignment_id, monday_str):
    _require_contractor()
    assignment = _own_assignment(assignment_id)
    monday = _parse_date(monday_str)
    state = week_state(assignment, monday)
    if not state["editable"]:
        flash("That week can't be submitted any more.", "error")
    elif not state["entries"] and not state["expenses"]:
        flash("Log some hours or a receipt first.", "error")
    elif state["submitted"]:
        flash("Already submitted.", "info")
    else:
        db.session.add(WeekSubmission(assignment_id=assignment.id, week_start=monday))
        db.session.commit()
        flash("Week submitted. You can still fix something until Verde approves it.", "success")
    return redirect(_back_to_week(assignment, monday))


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------

@contractor_bp.route("/receipts")
@login_required
def receipts():
    """Tab entry point: straight to the receipts screen for the contractor's
    assignment, or a picker if they have more than one."""
    _require_contractor()
    assignments = _active_assignments()
    if len(assignments) == 1:
        return redirect(url_for("contractor.expenses", assignment_id=assignments[0].id))
    return render_template("contractor_pick.html", assignments=assignments)


@contractor_bp.route("/expenses/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def expenses(assignment_id):
    _require_contractor()
    assignment = _own_assignment(assignment_id)
    today = business_today()
    this_monday, _ = week_bounds(today)

    if request.method == "POST":
        photo = request.files.get("photo")
        description = (request.form.get("description") or "").strip()[:300]
        category = (request.form.get("category") or "").strip()
        if category not in billing.EXPENSE_CATEGORIES:
            category = "Other"
        confirmed_amount = (request.form.get("amount") or "").strip()
        try:
            expense_date = datetime.strptime(request.form.get("expense_date", ""), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            expense_date = today

        monday, _ = week_bounds(expense_date)
        state = week_state(assignment, monday, today)
        if not state["editable"]:
            flash("That week is locked, so a receipt can't be added to it. Ask your contact at Verde.", "error")
            return redirect(url_for("contractor.expenses", assignment_id=assignment.id))

        if not photo or photo.filename == "" or not _allowed_file(photo.filename):
            flash("Please attach a photo of the receipt (jpg, png, heic or webp).", "error")
            return redirect(url_for("contractor.expenses", assignment_id=assignment.id))

        ext = photo.filename.rsplit(".", 1)[1].lower()
        stem = uuid.uuid4().hex
        raw_name = secure_filename(f"{stem}.{ext}")
        raw_path = os.path.join(current_app.config["UPLOAD_FOLDER"], raw_name)
        photo.save(raw_path)

        filename = f"{stem}.jpg"
        save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        if not _normalise_photo(raw_path, save_path):
            filename, save_path = raw_name, raw_path

        ocr_amount, confidence = extract_amount_from_receipt(save_path)

        final_amount, is_confirmed = None, False
        if confirmed_amount:
            try:
                final_amount = float(confirmed_amount)
                is_confirmed = True
            except ValueError:
                final_amount = ocr_amount
        else:
            final_amount = ocr_amount

        db.session.add(Expense(
            assignment_id=assignment.id, expense_date=expense_date, photo_filename=filename,
            amount=final_amount, ocr_amount=ocr_amount, ocr_confidence=confidence,
            is_amount_confirmed=is_confirmed, description=description, category=category,
        ))
        db.session.commit()
        _sync_packet(assignment, monday)
        db.session.commit()

        if is_confirmed:
            flash("Receipt added.", "success")
        elif ocr_amount:
            flash(f"Receipt added. We read ${ocr_amount:.2f} from the photo. Check it and tap Confirm.", "success")
        else:
            flash("Receipt added, but the amount couldn't be read. Type it in and tap Confirm.", "error")
        return redirect(url_for("contractor.expenses", assignment_id=assignment.id))

    this_week = week_state(assignment, this_monday, today)
    last_week = week_state(assignment, this_monday - timedelta(days=7), today)
    return render_template(
        "contractor_expenses.html", assignment=assignment, today=today,
        this_week=this_week, last_week=last_week,
        categories=list(billing.EXPENSE_CATEGORIES.keys()),
        min_date=(this_monday - timedelta(days=7)).isoformat(), max_date=today.isoformat(),
    )


@contractor_bp.route("/expenses/<int:expense_id>/confirm", methods=["POST"])
@login_required
def confirm_expense_amount(expense_id):
    _require_contractor()
    expense = Expense.query.get_or_404(expense_id)
    if expense.assignment.contractor_id != current_user.id:
        abort(403)

    monday, _ = week_bounds(expense.expense_date)
    if not week_state(expense.assignment, monday)["editable"]:
        flash("That week is locked. Ask your contact at Verde if the amount needs changing.", "error")
        return redirect(url_for("contractor.expenses", assignment_id=expense.assignment_id))

    try:
        expense.amount = float((request.form.get("amount") or "").strip())
        expense.is_amount_confirmed = True
        db.session.commit()
        _sync_packet(expense.assignment, monday)
        db.session.commit()
        flash("Amount confirmed.", "success")
    except ValueError:
        flash("Enter a valid amount.", "error")
    return redirect(url_for("contractor.expenses", assignment_id=expense.assignment_id))


@contractor_bp.route("/expenses/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(expense_id):
    """Wrong photo or a duplicate: removable while the week is still open."""
    _require_contractor()
    expense = Expense.query.get_or_404(expense_id)
    assignment = expense.assignment
    if assignment.contractor_id != current_user.id:
        abort(403)
    monday, _ = week_bounds(expense.expense_date)
    if not week_state(assignment, monday)["editable"]:
        flash("That week is locked, so this receipt can't be removed.", "error")
        return redirect(url_for("contractor.expenses", assignment_id=assignment.id))

    filename = expense.photo_filename
    db.session.delete(expense)
    db.session.commit()
    try:
        path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename or "")
        if filename and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
    _sync_packet(assignment, monday)
    db.session.commit()
    flash("Receipt removed.", "success")
    return redirect(url_for("contractor.expenses", assignment_id=assignment.id))


# --------------------------------------------------------------------------
# Past weeks
# --------------------------------------------------------------------------

@contractor_bp.route("/history")
@login_required
def history():
    _require_contractor()
    today = business_today()
    this_monday, _ = week_bounds(today)
    assignments = current_user.assignments.order_by(Assignment.active.desc(), Assignment.id).all()

    rows = []
    for a in assignments:
        start = a.start_date or this_monday
        for i in range(HISTORY_WEEKS):
            monday = this_monday - timedelta(days=7 * i)
            if monday + timedelta(days=6) < start:
                break
            if a.end_date and monday > a.end_date:
                continue
            w = week_state(a, monday, today)
            if i > 0 and not w["entries"] and not w["expenses"] and not w["packet"]:
                continue  # skip empty historic weeks
            rows.append(w)
    rows.sort(key=lambda w: (w["monday"], w["assignment"].id), reverse=True)

    total_hours = round(sum(w["hours"] for w in rows), 2)
    return render_template("contractor_history.html", rows=rows, total_hours=total_hours, this_monday=this_monday)


@contractor_bp.route("/week/<int:assignment_id>/<monday_str>")
@login_required
def week(assignment_id, monday_str):
    _require_contractor()
    assignment = _own_assignment(assignment_id)
    monday = _parse_date(monday_str)
    if monday.weekday() != 0:
        monday, _ = week_bounds(monday)
    state = week_state(assignment, monday)
    return render_template("contractor_week.html", state=state, assignment=assignment)
