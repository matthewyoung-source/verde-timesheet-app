"""One place that turns an assignment's week into a WeeklyPacket: builds the
PDF, works out the billing figures, and creates / updates the Xero draft.
Used by the Sunday job and by the admin packet screen so both behave the same."""

import io
import os
from datetime import date

from pypdf import PdfReader, PdfWriter

from flask import current_app

from extensions import db
from models import TimesheetEntry, Expense, WeeklyPacket
from pdf_generator import build_weekly_pdf
import billing
import xero_integration


class _ExpenseRow:
    """Expense plus the absolute path of its photo, for the PDF builder."""

    def __init__(self, exp, upload_folder):
        self.expense_date = exp.expense_date
        self.amount = exp.amount
        self.description = exp.description
        self.category = exp.category
        self.photo_path = os.path.join(upload_folder, exp.photo_filename) if exp.photo_filename else None


def week_rows(assignment, week_start, week_end):
    entries = assignment.timesheet_entries.filter(
        TimesheetEntry.work_date >= week_start, TimesheetEntry.work_date <= week_end
    ).order_by(TimesheetEntry.work_date).all()
    expenses = assignment.expenses.filter(
        Expense.expense_date >= week_start, Expense.expense_date <= week_end
    ).order_by(Expense.expense_date).all()
    return entries, expenses


def packet_filename(assignment, week_start):
    contractor = assignment.contractor.name.replace(" ", "_")
    client = assignment.client.name.replace(" ", "_")
    return f"timesheet_{contractor}_{client}_{week_start.isoformat()}.pdf"


def build_pdf(assignment, week_start, week_end, entries, expenses, filename, per_diem_days=None):
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    output_path = os.path.join(current_app.config["PDF_FOLDER"], filename)
    rows = [_ExpenseRow(e, upload_folder) for e in expenses]
    return build_weekly_pdf(
        output_path=output_path,
        assignment=assignment,
        week_start=week_start,
        week_end=week_end,
        timesheet_entries=entries,
        expenses=rows,
        per_diem_days=per_diem_days,
    )


def apply_figures(packet, figures, reference):
    packet.total_hours = figures["total_hours"]
    packet.regular_hours = figures["regular_hours"]
    packet.overtime_hours = figures["overtime_hours"]
    packet.per_diem_days = figures["per_diem_days"]
    packet.total_expenses = figures["total_expenses"]
    packet.xero_reference = reference


def create_packet(assignment, week_start, week_end):
    """Sunday job: build the PDF, create the Xero draft, record the packet."""
    entries, expenses = week_rows(assignment, week_start, week_end)
    filename = packet_filename(assignment, week_start)
    figures = build_pdf(assignment, week_start, week_end, entries, expenses, filename)
    reference = billing.xero_reference(assignment, week_end)

    invoice_id, invoice_status = xero_integration.create_draft_invoice(
        client_id=current_app.config["XERO_CLIENT_ID"],
        client_secret=current_app.config["XERO_CLIENT_SECRET"],
        client_name=assignment.client.name,
        client_xero_contact_id=assignment.client.xero_contact_id,
        lines=figures["lines"],
        reference=reference,
        invoice_date=date.today(),
    )

    client_approval_status = (
        "pending" if assignment.client.requires_client_approval else "not_required"
    )
    packet = WeeklyPacket(
        assignment_id=assignment.id,
        week_start=week_start,
        week_end=week_end,
        pdf_filename=filename,
        xero_invoice_id=invoice_id,
        xero_invoice_status=invoice_status,
        client_approval_status=client_approval_status,
    )
    apply_figures(packet, figures, reference)
    db.session.add(packet)
    return packet


def regenerate_packet(packet, entries, expenses, per_diem_days=None):
    """Admin edited hours / amounts / per diem days: rebuild the PDF in place,
    refresh the stored figures, and push the new lines to the Xero draft."""
    assignment = packet.assignment
    if not packet.pdf_filename:
        packet.pdf_filename = packet_filename(assignment, packet.week_start)
    figures = build_pdf(
        assignment, packet.week_start, packet.week_end, entries, expenses,
        packet.pdf_filename, per_diem_days=per_diem_days,
    )
    reference = billing.xero_reference(assignment, packet.week_end)
    apply_figures(packet, figures, reference)

    if packet.xero_invoice_id:
        status = xero_integration.update_draft_invoice(
            client_id=current_app.config["XERO_CLIENT_ID"],
            client_secret=current_app.config["XERO_CLIENT_SECRET"],
            invoice_id=packet.xero_invoice_id,
            lines=figures["lines"],
            reference=reference,
        )
        if status == "updated":
            packet.xero_invoice_status = "draft_created"
        elif status == "failed":
            packet.xero_invoice_status = "failed"
    elif packet.xero_invoice_status in (None, "not_connected", "failed"):
        # No draft yet (Xero wasn't connected when the packet was generated,
        # or the first attempt failed) -- try to create one now.
        invoice_id, invoice_status = xero_integration.create_draft_invoice(
            client_id=current_app.config["XERO_CLIENT_ID"],
            client_secret=current_app.config["XERO_CLIENT_SECRET"],
            client_name=assignment.client.name,
            client_xero_contact_id=assignment.client.xero_contact_id,
            lines=figures["lines"],
            reference=reference,
            invoice_date=date.today(),
        )
        packet.xero_invoice_id = invoice_id
        packet.xero_invoice_status = invoice_status
    return figures


def full_packet_pdf(packet):
    """Xero invoice (page 1) followed by the timesheet packet, as one PDF in
    memory -- the same bundle Matthew used to assemble by hand. If the invoice
    can't be fetched (Xero not connected, draft deleted), returns just the packet."""
    packet_path = os.path.join(current_app.config["PDF_FOLDER"], packet.pdf_filename or "")
    if not packet.pdf_filename or not os.path.exists(packet_path):
        return None, False

    writer = PdfWriter()
    invoice_included = False
    invoice_bytes = xero_integration.fetch_invoice_pdf(
        current_app.config["XERO_CLIENT_ID"],
        current_app.config["XERO_CLIENT_SECRET"],
        packet.xero_invoice_id,
    )
    if invoice_bytes:
        try:
            for page in PdfReader(io.BytesIO(invoice_bytes)).pages:
                # Xero pads some invoices with a blank trailing page; skip pages with no text.
                if (page.extract_text() or "").strip():
                    writer.add_page(page)
            invoice_included = True
        except Exception:
            invoice_included = False

    for page in PdfReader(packet_path).pages:
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out, invoice_included
