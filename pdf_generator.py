"""Builds the combined timesheet + expense PDF for one contractor,
one assignment (one client), for one Sunday-to-Saturday week."""

import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image as RLImage,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

GREEN = colors.HexColor("#2f6f3e")  # Verde Solutions brand-ish green


def build_weekly_pdf(output_path, company_name, contractor_name, client_name,
                      role_title, week_start, week_end, billing_rate,
                      timesheet_entries, expenses):
    """timesheet_entries: list of TimesheetEntry-like objects (work_date, hours, notes)
    expenses: list of Expense-like objects (expense_date, amount, description, photo_path)
    """
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleGreen", parent=styles["Title"], textColor=GREEN, fontSize=20
    )
    heading_style = ParagraphStyle(
        "HeadingGreen", parent=styles["Heading2"], textColor=GREEN, spaceBefore=16
    )
    normal = styles["Normal"]

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
    )

    elements = []
    elements.append(Paragraph(company_name, title_style))
    elements.append(Paragraph("Weekly Timesheet and Expense Report", styles["Heading3"]))
    elements.append(Spacer(1, 10))

    header_data = [
        ["Contractor:", contractor_name, "Client:", client_name],
        ["Role:", role_title or "-", "Billing rate:", f"${billing_rate:.2f}/hr"],
        ["Week:", f"{week_start.strftime('%b %d, %Y')} - {week_end.strftime('%b %d, %Y')}", "", ""],
    ]
    header_table = Table(header_data, colWidths=[1.0 * inch, 2.4 * inch, 1.0 * inch, 2.0 * inch])
    header_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(header_table)
    elements.append(Spacer(1, 14))

    # Timesheet section
    elements.append(Paragraph("Hours Worked", heading_style))
    ts_data = [["Date", "Day", "Hours", "Notes"]]
    total_hours = 0
    for entry in sorted(timesheet_entries, key=lambda e: e.work_date):
        total_hours += float(entry.hours)
        ts_data.append(
            [
                entry.work_date.strftime("%m/%d/%Y"),
                entry.work_date.strftime("%A"),
                f"{float(entry.hours):.2f}",
                entry.notes or "",
            ]
        )
    ts_data.append(["", "", f"{total_hours:.2f}", "TOTAL HOURS"])

    ts_table = Table(ts_data, colWidths=[1.1 * inch, 1.1 * inch, 0.9 * inch, 3.3 * inch])
    ts_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -2), 0.25, colors.lightgrey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(ts_table)
    elements.append(Spacer(1, 18))

    # Expenses section
    elements.append(Paragraph("Expenses", heading_style))
    if expenses:
        exp_data = [["Date", "Description", "Amount"]]
        total_expenses = 0.0
        for exp in sorted(expenses, key=lambda e: e.expense_date):
            amount = float(exp.amount) if exp.amount is not None else 0.0
            total_expenses += amount
            exp_data.append(
                [
                    exp.expense_date.strftime("%m/%d/%Y"),
                    exp.description or "Receipt",
                    f"${amount:.2f}",
                ]
            )
        exp_data.append(["", "TOTAL EXPENSES", f"${total_expenses:.2f}"])

        exp_table = Table(exp_data, colWidths=[1.1 * inch, 4.3 * inch, 1.0 * inch])
        exp_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
                    ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -2), 0.25, colors.lightgrey),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        elements.append(exp_table)
        elements.append(Spacer(1, 12))

        # Receipt images, one per page-friendly row
        elements.append(Paragraph("Receipt Images", heading_style))
        for exp in sorted(expenses, key=lambda e: e.expense_date):
            if exp.photo_path and os.path.exists(exp.photo_path):
                caption = f"{exp.expense_date.strftime('%m/%d/%Y')} - ${float(exp.amount or 0):.2f} - {exp.description or ''}"
                elements.append(Paragraph(caption, normal))
                try:
                    img = RLImage(exp.photo_path, width=3.0 * inch, height=3.0 * inch, kind="proportional")
                    elements.append(img)
                except Exception:
                    elements.append(Paragraph("(receipt image could not be embedded)", normal))
                elements.append(Spacer(1, 10))
    else:
        elements.append(Paragraph("No expenses submitted this week.", normal))
        total_expenses = 0.0

    elements.append(Spacer(1, 16))
    note_style = ParagraphStyle("Note", parent=normal, fontSize=8, textColor=colors.grey)
    elements.append(
        Paragraph(
            "Note: this document reflects hours and expenses as submitted by the contractor. "
            "The client invoice is calculated from hours worked only.",
            note_style,
        )
    )

    doc.build(elements)
    return {"total_hours": total_hours, "total_expenses": total_expenses}
