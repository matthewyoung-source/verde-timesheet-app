import os
import uuid
from datetime import datetime, timedelta

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
from models import Assignment, TimesheetEntry, Expense, WeeklyPacket
from ocr import extract_amount_from_receipt
from utils import week_bounds
import billing

contractor_bp = Blueprint("contractor", __name__, url_prefix="/my")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "heic", "heif", "webp"}
MAX_PHOTO_EDGE = 2000  # px -- plenty for a legible receipt, keeps PDFs small


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
    if current_user.is_admin():
        abort(403)


@contractor_bp.route("/")
@login_required
def dashboard():
    _require_contractor()
    monday, sunday = week_bounds()
    assignments = current_user.assignments.filter_by(active=True).all()

    week_summary = []
    for a in assignments:
        entries = a.timesheet_entries.filter(
            TimesheetEntry.work_date >= monday, TimesheetEntry.work_date <= sunday
        ).all()
        expenses = a.expenses.filter(
            Expense.expense_date >= monday, Expense.expense_date <= sunday
        ).all()
        week_summary.append(
            {
                "assignment": a,
                "hours_logged": sum(float(e.hours) for e in entries),
                "days_logged": len(entries),
                "expense_count": len(expenses),
            }
        )

    return render_template(
        "contractor_dashboard.html",
        week_summary=week_summary,
        monday=monday,
        sunday=sunday,
    )


@contractor_bp.route("/timesheet/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def timesheet(assignment_id):
    _require_contractor()
    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.contractor_id != current_user.id:
        abort(403)

    monday, sunday = week_bounds()
    days = [monday + timedelta(days=i) for i in range(7)]

    packet = WeeklyPacket.query.filter_by(assignment_id=assignment.id, week_start=monday).first()
    locked = packet.is_locked() if packet else False

    existing = {
        e.work_date: e
        for e in assignment.timesheet_entries.filter(
            TimesheetEntry.work_date >= monday, TimesheetEntry.work_date <= sunday
        ).all()
    }

    if request.method == "POST":
        if locked:
            flash("This week has already been approved and locked -- ask Matthew if it needs a change.", "error")
            return redirect(url_for("contractor.dashboard"))

        for day in days:
            key = day.isoformat()
            start_t = _parse_time(request.form.get(f"start_{key}", ""))
            end_t = _parse_time(request.form.get(f"end_{key}", ""))
            hours_raw = request.form.get(f"hours_{key}", "").strip()
            notes = request.form.get(f"notes_{key}", "").strip()

            # Start + end times win; otherwise fall back to a typed hours figure.
            hours = None
            if start_t and end_t:
                hours = billing.hours_from_times(start_t, end_t, assignment.daily_break_hours)
            elif hours_raw:
                try:
                    hours = float(hours_raw)
                except ValueError:
                    hours = None
                start_t = end_t = None

            if hours is None:
                if day in existing:
                    db.session.delete(existing[day])
                continue

            if day in existing:
                existing[day].hours = hours
                existing[day].start_time = start_t
                existing[day].end_time = end_t
                existing[day].notes = notes
            else:
                db.session.add(
                    TimesheetEntry(
                        assignment_id=assignment.id, work_date=day, hours=hours,
                        start_time=start_t, end_time=end_t, notes=notes,
                    )
                )

        db.session.commit()
        flash("Timesheet saved.", "success")
        return redirect(url_for("contractor.dashboard"))

    return render_template(
        "timesheet_form.html", assignment=assignment, days=days, existing=existing,
        monday=monday, sunday=sunday, locked=locked,
    )


def _parse_time(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


@contractor_bp.route("/expenses/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def expenses(assignment_id):
    _require_contractor()
    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.contractor_id != current_user.id:
        abort(403)

    monday, sunday = week_bounds()

    packet = WeeklyPacket.query.filter_by(assignment_id=assignment.id, week_start=monday).first()
    locked = packet.is_locked() if packet else False

    if request.method == "POST":
        if locked:
            flash("This week has already been approved and locked -- ask Matthew if it needs a change.", "error")
            return redirect(url_for("contractor.expenses", assignment_id=assignment.id))

        photo = request.files.get("photo")
        expense_date_raw = request.form.get("expense_date")
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        if category not in billing.EXPENSE_CATEGORIES:
            category = "Other"
        confirmed_amount = request.form.get("amount", "").strip()

        if not photo or photo.filename == "" or not _allowed_file(photo.filename):
            flash("Please attach a valid photo (jpg, png, heic, webp).", "error")
            return redirect(url_for("contractor.expenses", assignment_id=assignment.id))

        ext = photo.filename.rsplit(".", 1)[1].lower()
        stem = uuid.uuid4().hex
        raw_name = secure_filename(f"{stem}.{ext}")
        raw_path = os.path.join(current_app.config["UPLOAD_FOLDER"], raw_name)
        photo.save(raw_path)

        # Convert to JPEG so HEIC from iPhones (and oversized PNGs) work everywhere.
        filename = f"{stem}.jpg"
        save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        if not _normalise_photo(raw_path, save_path):
            filename, save_path = raw_name, raw_path

        ocr_amount, confidence = extract_amount_from_receipt(save_path)

        try:
            expense_date = datetime.strptime(expense_date_raw, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            expense_date = monday

        final_amount = None
        is_confirmed = False
        if confirmed_amount:
            try:
                final_amount = float(confirmed_amount)
                is_confirmed = True
            except ValueError:
                final_amount = ocr_amount
        else:
            final_amount = ocr_amount

        expense = Expense(
            assignment_id=assignment.id,
            expense_date=expense_date,
            photo_filename=filename,
            amount=final_amount,
            ocr_amount=ocr_amount,
            ocr_confidence=confidence,
            is_amount_confirmed=is_confirmed,
            description=description,
            category=category,
        )
        db.session.add(expense)
        db.session.commit()
        flash("Expense submitted." + (" We auto-read the amount, please double check it." if not is_confirmed else ""), "success")
        return redirect(url_for("contractor.dashboard"))

    week_expenses = assignment.expenses.filter(
        Expense.expense_date >= monday, Expense.expense_date <= sunday
    ).order_by(Expense.expense_date.desc()).all()

    return render_template(
        "expense_form.html", assignment=assignment, monday=monday, sunday=sunday,
        week_expenses=week_expenses, locked=locked,
        categories=list(billing.EXPENSE_CATEGORIES.keys()),
    )


@contractor_bp.route("/expenses/<int:expense_id>/confirm", methods=["POST"])
@login_required
def confirm_expense_amount(expense_id):
    _require_contractor()
    expense = Expense.query.get_or_404(expense_id)
    if expense.assignment.contractor_id != current_user.id:
        abort(403)

    exp_monday, _ = week_bounds(expense.expense_date)
    packet = WeeklyPacket.query.filter_by(assignment_id=expense.assignment_id, week_start=exp_monday).first()
    if packet and packet.is_locked():
        flash("This week has already been approved and locked -- ask Matthew if it needs a change.", "error")
        return redirect(url_for("contractor.expenses", assignment_id=expense.assignment_id))

    amount_raw = request.form.get("amount", "").strip()
    try:
        expense.amount = float(amount_raw)
        expense.is_amount_confirmed = True
        db.session.commit()
        flash("Amount confirmed.", "success")
    except ValueError:
        flash("Enter a valid amount.", "error")

    return redirect(url_for("contractor.expenses", assignment_id=expense.assignment_id))
