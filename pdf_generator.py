"""Builds the weekly Timesheet & Expense Report packet for one contractor on
one assignment, Monday to Sunday, in the Verde Solutions layout:

    page 1  header band + details + TIME WORKED table + hours totals
    page 2  EXPENSES table (per diem rows + receipts) + totals + signatures
    page 3+ one receipt photo per page

Colours and layout follow the Verde Excel template (verde-solutions.net palette).
"""

import io
import os
from datetime import timedelta

from PIL import Image as PILImage, ImageOps

try:  # iPhone photos arrive as HEIC; this teaches Pillow to open them.
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.utils import ImageReader

import billing

# verde-solutions.net palette
FOREST = colors.HexColor("#0F2F24")
GREEN = colors.HexColor("#1F7A5A")
AMBER = colors.HexColor("#F2A93B")
INK = colors.HexColor("#172320")
PAPER = colors.HexColor("#F6F5F1")
LINE = colors.HexColor("#E6E4DC")
MUTE = colors.HexColor("#7A8580")
WHITE = colors.white

LOGO_PATH = os.path.join(os.path.dirname(__file__), "static", "logo.png")

PAGE_W, PAGE_H = letter
MARGIN = 0.55 * inch
CONTENT_W = PAGE_W - 2 * MARGIN
BAND_H = 0.95 * inch

_base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=12, textColor=INK)
_label = ParagraphStyle("label", parent=_base, fontName="Helvetica-Bold", textColor=GREEN, fontSize=9)
_value = ParagraphStyle("value", parent=_base, fontSize=10.5)
_section = ParagraphStyle("section", parent=_base, fontName="Helvetica-Bold", fontSize=11, textColor=FOREST, spaceBefore=4, spaceAfter=2)
_caption = ParagraphStyle("caption", parent=_base, fontName="Helvetica-Bold", fontSize=10, textColor=FOREST)
_sig = ParagraphStyle("sig", parent=_base, fontName="Helvetica-Bold", fontSize=8, textColor=GREEN)
_muted = ParagraphStyle("muted", parent=_base, fontSize=8, textColor=MUTE)
_right = ParagraphStyle("right", parent=_base, alignment=TA_RIGHT)


def _fmt_time(t):
    if t is None:
        return ""
    return t.strftime("%-I:%M%p").lower()


def _fmt_money(v):
    return f"${v:,.2f}"


def _receipt_flowable(photo_path, max_w, max_h):
    """Opens the receipt with Pillow (HEIC included), fixes orientation, and
    hands ReportLab a clean JPEG. Returns None if the file can't be read, so
    a bad photo never brings the whole packet down."""
    try:
        with PILImage.open(photo_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((1600, 1600))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            w, h = im.size
        buf.seek(0)
        scale = min(max_w / w, max_h / h, 1.0)
        img = RLImage(buf, width=w * scale, height=h * scale)
        img.hAlign = "LEFT"
        return img
    except Exception:
        return None


def _draw_band(canvas, doc, title):
    """Dark green header band with the logo, amber keyline under it."""
    canvas.saveState()
    top = PAGE_H - 0.35 * inch
    canvas.setFillColor(FOREST)
    canvas.rect(MARGIN, top - BAND_H, CONTENT_W, BAND_H, stroke=0, fill=1)
    canvas.setFillColor(AMBER)
    canvas.rect(MARGIN, top - BAND_H - 3, CONTENT_W, 3, stroke=0, fill=1)

    if os.path.exists(LOGO_PATH):
        try:
            img = ImageReader(LOGO_PATH)
            iw, ih = img.getSize()
            logo_h = BAND_H * 0.62
            logo_w = logo_h * iw / ih
            canvas.drawImage(img, MARGIN + 0.18 * inch, top - BAND_H / 2 - logo_h / 2,
                             width=logo_w, height=logo_h, mask="auto")
        except Exception:
            pass

    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawRightString(MARGIN + CONTENT_W - 0.2 * inch, top - BAND_H / 2 - 5, title)
    canvas.restoreState()


def _section_heading(text):
    """Section title with an amber rule beneath, as on the Excel template."""
    t = Table([[Paragraph(text, _section)]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 1.5, AMBER),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _grid_style(n_rows, align_cols=None):
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), FOREST),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
    ]
    for r in range(1, n_rows):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), PAPER))
    for col, al in (align_cols or {}).items():
        style.append(("ALIGN", (col, 1), (col, -1), al))
    return TableStyle(style)


def _totals_block(rows):
    """rows: list of (label, value_text, strong). Right-aligned mini table."""
    data = [[Paragraph(f"<b>{lbl}</b>" if strong else lbl, _right), val] for lbl, val, strong in rows]
    t = Table(data, colWidths=[3.0 * inch, 1.3 * inch], hAlign="RIGHT")
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (1, 0), (1, -1), 0.5, LINE),
        ("INNERGRID", (1, 0), (1, -1), 0.5, LINE),
        ("BACKGROUND", (1, 0), (1, -1), PAPER),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, (_, _, strong) in enumerate(rows):
        if strong:
            style += [
                ("BACKGROUND", (1, i), (1, i), AMBER),
                ("TEXTCOLOR", (1, i), (1, i), FOREST),
                ("FONTSIZE", (1, i), (1, i), 11),
            ]
    t.setStyle(TableStyle(style))
    return t


def build_weekly_pdf(output_path, assignment, week_start, week_end,
                     timesheet_entries, expenses, per_diem_days=None):
    """timesheet_entries: TimesheetEntry-like (work_date, hours, start_time, end_time)
    expenses: objects with expense_date, amount, description, category, photo_path
    Returns the billing.compute_week() dict (totals, invoice lines)."""

    figures = billing.compute_week(assignment, timesheet_entries, expenses, per_diem_days)
    contractor = assignment.contractor
    title = "TIMESHEET  &  EXPENSE REPORT"

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        topMargin=0.35 * inch + BAND_H + 0.3 * inch, bottomMargin=0.6 * inch,
        leftMargin=MARGIN, rightMargin=MARGIN,
    )

    def on_page(canvas, doc_):
        _draw_band(canvas, doc_, title)
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTE)
        canvas.drawRightString(PAGE_W - MARGIN, 0.4 * inch,
                               f"{contractor.name}  |  W/E {week_end.strftime('%m/%d/%Y')}  |  Page {doc_.page}")
        canvas.restoreState()

    el = []

    # ---------- details ----------
    details = [
        [Paragraph("Employee", _label), Paragraph(contractor.name, _value),
         Paragraph("PO #", _label), Paragraph(assignment.po_number or "", _value)],
        [Paragraph("Client", _label), Paragraph(assignment.client.name, _value),
         Paragraph("Period Start Date", _label), Paragraph(week_start.strftime("%m/%d/%Y"), _value)],
        [Paragraph("Email", _label), Paragraph(contractor.email, _value),
         Paragraph("Period End Date", _label), Paragraph(week_end.strftime("%m/%d/%Y"), _value)],
    ]
    dt = Table(details, colWidths=[1.1 * inch, 2.6 * inch, 1.3 * inch, 2.1 * inch])
    dt.setStyle(TableStyle([
        ("LINEBELOW", (1, 0), (1, -1), 0.5, LINE),
        ("LINEBELOW", (3, 0), (3, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el += [dt, Spacer(1, 14)]

    # ---------- time worked ----------
    el.append(_section_heading("TIME WORKED"))
    by_date = {e.work_date: e for e in timesheet_entries}
    rows = [["Date", "Start Time", "End Time", "Total Hours", "Overtime?"]]
    for day in billing.week_days(week_start):
        e = by_date.get(day)
        rows.append([
            day.strftime("%a  %m/%d/%Y"),
            _fmt_time(e.start_time) if e else "",
            _fmt_time(e.end_time) if e else "",
            f"{float(e.hours):.2f}" if e else "",
            "",
        ])
    tt = Table(rows, colWidths=[1.55 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch, 1.65 * inch])
    tt.setStyle(_grid_style(len(rows), {0: "CENTER", 1: "CENTER", 2: "CENTER", 3: "CENTER"}))
    el += [tt, Spacer(1, 12)]
    el.append(_totals_block([
        ("Regular Hours", f"{figures['regular_hours']:.2f}", False),
        ("Overtime Hours", f"{figures['overtime_hours']:.2f}", False),
        ("TOTAL HOURS WORKED", f"{figures['total_hours']:.2f}", True),
    ]))

    # ---------- expenses ----------
    el.append(PageBreak())
    el.append(_section_heading("EXPENSES"))
    rows = [["Date", "Description", "Amount", "Receipt?"]]
    pd_rate = figures["per_diem_contractor_rate"]
    pd_days = figures["per_diem_days"]
    if pd_rate > 0 and pd_days > 0:
        for i, day in enumerate(billing.week_days(week_start)):
            if i >= pd_days:
                break
            rows.append([day.strftime("%a  %m/%d/%Y"), "Per Diem", _fmt_money(pd_rate), ""])
    sorted_expenses = sorted(expenses, key=lambda x: x.expense_date)
    for exp in sorted_expenses:
        label = exp.category or "Expense"
        if exp.description:
            label = f"{label}  -  {exp.description}"
        rows.append([
            exp.expense_date.strftime("%a  %m/%d/%Y"),
            label,
            _fmt_money(float(exp.amount or 0)),
            "Yes" if getattr(exp, "photo_path", None) else "",
        ])
    if len(rows) == 1:
        rows.append(["", "No expenses submitted this week.", "", ""])
    et = Table(rows, colWidths=[1.55 * inch, 3.3 * inch, 1.1 * inch, 1.15 * inch])
    et.setStyle(_grid_style(len(rows), {0: "CENTER", 1: "LEFT", 2: "RIGHT", 3: "CENTER"}))
    el += [et, Spacer(1, 12)]
    el.append(_totals_block([
        (f"Total Per Diem  ({pd_days} days @ {_fmt_money(pd_rate)})" if pd_rate else "Total Per Diem",
         _fmt_money(figures["per_diem_contractor_total"]), False),
        ("Items to Expense", _fmt_money(figures["total_expenses"]), False),
        ("TOTAL EXPENSES", _fmt_money(figures["per_diem_contractor_total"] + figures["total_expenses"]), True),
    ]))

    el.append(Spacer(1, 40))
    sig = Table([
        ["", "", "", ""],
        [Paragraph("Contractor Signature", _sig), "", Paragraph("Approved By", _sig), ""],
    ], colWidths=[2.2 * inch, 0.6 * inch, 2.2 * inch, 2.2 * inch])
    sig.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (0, 0), 0.75, GREEN),
        ("LINEBELOW", (2, 0), (3, 0), 0.75, GREEN),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    el.append(sig)

    # ---------- receipts, one per page ----------
    receipt_count = 0
    for exp in sorted_expenses:
        photo_path = getattr(exp, "photo_path", None)
        if not photo_path or not os.path.exists(photo_path):
            continue
        receipt_count += 1
        el.append(PageBreak())
        if receipt_count == 1:
            el.append(_section_heading("RECEIPTS"))
            el.append(Spacer(1, 6))
        caption = (f"{exp.category or 'Expense'}  -  {exp.description or ''}  -  "
                   f"{exp.expense_date.strftime('%m/%d/%Y')}  -  {_fmt_money(float(exp.amount or 0))}")
        el.append(Paragraph(caption, _caption))
        el.append(Spacer(1, 6))
        img = _receipt_flowable(photo_path, 5.6 * inch, 7.2 * inch)
        if img is not None:
            el.append(img)
        else:
            el.append(Paragraph("(receipt image could not be embedded)", _muted))

    doc.build(el, onFirstPage=on_page, onLaterPages=on_page)
    return figures
