import os
from datetime import date
from zoneinfo import ZoneInfo

from extensions import db
from models import Assignment, TimesheetEntry, Expense, WeeklyPacket, User
from pdf_generator import build_weekly_pdf
from utils import week_bounds
import notifications
import xero_integration


def run_weekly_packet_job(app):
    """Runs every Sunday night: for every active assignment with at least
    one timesheet entry or expense logged this week, build the combined
    PDF and (if Xero is connected) create a draft invoice for hours only."""
    with app.app_context():
        monday, sunday = week_bounds(date.today())
        client_id = app.config["XERO_CLIENT_ID"]
        client_secret = app.config["XERO_CLIENT_SECRET"]

        assignments = Assignment.query.filter_by(active=True).all()

        for assignment in assignments:
            entries = assignment.timesheet_entries.filter(
                TimesheetEntry.work_date >= monday, TimesheetEntry.work_date <= sunday
            ).all()
            expenses = assignment.expenses.filter(
                Expense.expense_date >= monday, Expense.expense_date <= sunday
            ).all()

            if not entries and not expenses:
                continue

            existing_packet = WeeklyPacket.query.filter_by(
                assignment_id=assignment.id, week_start=monday
            ).first()
            if existing_packet:
                continue  # already generated for this week

            filename = f"timesheet_{assignment.contractor.name.replace(' ', '_')}_{assignment.client.name.replace(' ', '_')}_{monday.isoformat()}.pdf"
            output_path = os.path.join(app.config["PDF_FOLDER"], filename)

            expense_rows = []
            for exp in expenses:
                photo_path = os.path.join(app.config["UPLOAD_FOLDER"], exp.photo_filename)
                expense_rows.append(
                    type("ExpenseRow", (), {
                        "expense_date": exp.expense_date,
                        "amount": exp.amount,
                        "description": exp.description,
                        "photo_path": photo_path,
                    })
                )

            totals = build_weekly_pdf(
                output_path=output_path,
                company_name=app.config["COMPANY_NAME"],
                contractor_name=assignment.contractor.name,
                client_name=assignment.client.name,
                role_title=assignment.role_title,
                week_start=monday,
                week_end=sunday,
                billing_rate=float(assignment.billing_rate),
                timesheet_entries=entries,
                expenses=expense_rows,
            )

            invoice_id, invoice_status = xero_integration.create_draft_invoice(
                client_id=client_id,
                client_secret=client_secret,
                client_name=assignment.client.name,
                client_xero_contact_id=assignment.client.xero_contact_id,
                contractor_name=assignment.contractor.name,
                week_start=monday,
                week_end=sunday,
                hours=totals["total_hours"],
                billing_rate=float(assignment.billing_rate),
            )

            # Verde itself doesn't use this (Matthew approves verbally on a
            # Monday call), but for a resold instance where the client is
            # flagged for sign-off, mark this packet as awaiting their response.
            client_approval_status = (
                "pending" if assignment.client.requires_client_approval else "not_required"
            )

            packet = WeeklyPacket(
                assignment_id=assignment.id,
                week_start=monday,
                week_end=sunday,
                pdf_filename=filename,
                total_hours=totals["total_hours"],
                total_expenses=totals["total_expenses"],
                xero_invoice_id=invoice_id,
                xero_invoice_status=invoice_status,
                client_approval_status=client_approval_status,
            )
            db.session.add(packet)

        db.session.commit()


def run_daily_reminder_job(app):
    """Runs every day at 5pm Pacific (configurable): emails any contractor
    with an active assignment who hasn't logged an hours entry for today yet."""
    with app.app_context():
        if not notifications.is_configured(app):
            return  # nothing to do until SMTP is set up

        today = date.today()
        contractors = User.query.filter_by(role="contractor", active=True).all()

        for contractor in contractors:
            if contractor.assignments.filter_by(active=True).first() is None:
                continue

            logged_today = (
                TimesheetEntry.query.join(Assignment)
                .filter(Assignment.contractor_id == contractor.id, TimesheetEntry.work_date == today)
                .first()
            )
            if logged_today:
                continue

            dashboard_url = f"{app.config['APP_BASE_URL'].rstrip('/')}/my/"
            notifications.send_email(
                app,
                contractor.email,
                "Reminder: log today's hours and expenses",
                (
                    f"Hi {contractor.name},\n\n"
                    "Just a reminder to log today's hours (and any expenses) before the day "
                    f"closes out: {dashboard_url}\n\n"
                    "Thanks!"
                ),
            )


def register_jobs(app, scheduler):
    # Sunday at 11:50 PM -- captures the whole Monday-Sunday work week,
    # including any hours logged later that same Sunday.
    scheduler.add_job(
        func=lambda: run_weekly_packet_job(app),
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=50,
        id="weekly_packet_job",
        replace_existing=True,
    )

    # Daily nudge (5pm Pacific by default) to log today's time and expenses.
    scheduler.add_job(
        func=lambda: run_daily_reminder_job(app),
        trigger="cron",
        hour=app.config["REMINDER_HOUR"],
        minute=app.config["REMINDER_MINUTE"],
        timezone=ZoneInfo(app.config["REMINDER_TIMEZONE"]),
        id="daily_reminder_job",
        replace_existing=True,
    )
