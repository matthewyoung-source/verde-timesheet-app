import os
from datetime import date

from extensions import db
from models import Assignment, TimesheetEntry, Expense, WeeklyPacket
from pdf_generator import build_weekly_pdf
from utils import week_bounds
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

            packet = WeeklyPacket(
                assignment_id=assignment.id,
                week_start=monday,
                week_end=sunday,
                pdf_filename=filename,
                total_hours=totals["total_hours"],
                total_expenses=totals["total_expenses"],
                xero_invoice_id=invoice_id,
                xero_invoice_status=invoice_status,
            )
            db.session.add(packet)

        db.session.commit()


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
