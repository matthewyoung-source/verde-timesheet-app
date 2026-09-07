"""Turns one week's timesheet entries + expenses for an assignment into the
figures Verde bills: regular hours (capped at 40), overtime, per diem, and
expenses rolled up by type. Used by both the PDF packet and the Xero draft
invoice so the two always agree.

Mirrors how Verde's real invoices are laid out (e.g. INV177):

    Regular Hours   40.00 x $75.00
    Overtime         4.25 x $112.50   (1.5x unless the assignment sets a rate)
    Per Diem         7    x $178.00   (per_diem_days, default every day of the week)
    Fuel             1    x $77.77    (every fuel receipt summed into one line)
"""

from collections import OrderedDict
from datetime import timedelta

REGULAR_HOURS_CAP = 40.0

# Expense types a contractor can pick. The key is stored on Expense.category;
# same-type receipts merge into one invoice line. Xero item codes / account
# codes come from Verde's existing chart (RH/OT/PD/Fuel items, 41xx accounts).
EXPENSE_CATEGORIES = OrderedDict([
    ("Fuel", {"item_code": "Fuel", "account_code": "4160"}),
    ("Rental", {"item_code": "Rental", "account_code": "4160"}),
    ("Project Equipment", {"item_code": "12222", "account_code": "4160"}),
    ("Lodging", {"item_code": None, "account_code": "4160"}),
    ("Travel", {"item_code": None, "account_code": "4160"}),
    ("Other", {"item_code": None, "account_code": "4160"}),
])

HOURS_ITEM = {"item_code": "RH", "account_code": "4100"}
OVERTIME_ITEM = {"item_code": "OT", "account_code": "4140"}
PER_DIEM_ITEM = {"item_code": "PD", "account_code": "4150"}


def _money(value):
    return round(float(value or 0), 2)


def hours_from_times(start_time, end_time, break_hours=0):
    """Hours between two clock times (same day), less an unpaid break."""
    if start_time is None or end_time is None:
        return None
    start_minutes = start_time.hour * 60 + start_time.minute
    end_minutes = end_time.hour * 60 + end_time.minute
    if end_minutes <= start_minutes:
        end_minutes += 24 * 60  # crossed midnight
    hours = (end_minutes - start_minutes) / 60.0 - float(break_hours or 0)
    return round(max(hours, 0), 2)


def week_days(week_start):
    return [week_start + timedelta(days=i) for i in range(7)]


def compute_week(assignment, entries, expenses, per_diem_days=None):
    """Returns a dict with everything the PDF and the invoice need.

    entries:  TimesheetEntry-like objects (work_date, hours, start_time, end_time)
    expenses: Expense-like objects (expense_date, amount, description, category)
    per_diem_days: override for this week (admin edit); defaults to the assignment's.
    """
    total_hours = round(sum(float(e.hours or 0) for e in entries), 2)
    regular_hours = min(total_hours, REGULAR_HOURS_CAP)
    overtime_hours = round(max(total_hours - REGULAR_HOURS_CAP, 0), 2)

    billing_rate = _money(assignment.billing_rate)
    overtime_rate = assignment.effective_overtime_rate()

    if per_diem_days is None:
        per_diem_days = assignment.per_diem_days if assignment.per_diem_days is not None else 7
    per_diem_bill_rate = _money(assignment.per_diem_bill_rate)
    per_diem_contractor_rate = _money(assignment.per_diem_contractor_rate)

    # Expenses grouped by category (fall back to "Other" for older rows).
    groups = OrderedDict()
    total_expenses = 0.0
    for exp in expenses:
        amount = _money(exp.amount)
        total_expenses += amount
        category = getattr(exp, "category", None) or "Other"
        groups.setdefault(category, {"amount": 0.0, "count": 0})
        groups[category]["amount"] = round(groups[category]["amount"] + amount, 2)
        groups[category]["count"] += 1
    total_expenses = round(total_expenses, 2)

    # --- invoice lines, in the same order Verde's invoices use ---
    lines = []
    lines.append({
        "description": "Regular Hours",
        "quantity": regular_hours,
        "unit_amount": billing_rate,
        **HOURS_ITEM,
    })
    if overtime_hours > 0:
        lines.append({
            "description": "Overtime",
            "quantity": overtime_hours,
            "unit_amount": overtime_rate,
            **OVERTIME_ITEM,
        })
    if per_diem_bill_rate > 0 and per_diem_days > 0:
        lines.append({
            "description": "Per Diem",
            "quantity": float(per_diem_days),
            "unit_amount": per_diem_bill_rate,
            **PER_DIEM_ITEM,
        })
    for category, info in groups.items():
        if info["amount"] <= 0:
            continue
        codes = EXPENSE_CATEGORIES.get(category, EXPENSE_CATEGORIES["Other"])
        lines.append({
            "description": category,
            "quantity": 1.0,
            "unit_amount": info["amount"],
            **codes,
        })

    invoice_total = round(sum(l["quantity"] * l["unit_amount"] for l in lines), 2)

    return {
        "total_hours": total_hours,
        "regular_hours": regular_hours,
        "overtime_hours": overtime_hours,
        "billing_rate": billing_rate,
        "overtime_rate": overtime_rate,
        "per_diem_days": per_diem_days,
        "per_diem_bill_rate": per_diem_bill_rate,
        "per_diem_contractor_rate": per_diem_contractor_rate,
        "per_diem_contractor_total": round(per_diem_contractor_rate * per_diem_days, 2),
        "expense_groups": groups,
        "total_expenses": total_expenses,
        "lines": lines,
        "invoice_total": invoice_total,
    }


def xero_reference(assignment, week_end):
    """VerdeCC_Abner_Bell_09.06.2026 -- prefix from the assignment plus the
    week-ending date. Falls back to a readable default if no prefix is set."""
    prefix = (assignment.invoice_reference_prefix or "").strip()
    if not prefix:
        first_name = (assignment.contractor.name or "").split(" ")[0]
        prefix = f"Verde_{first_name}"
    return f"{prefix}_{week_end.strftime('%m.%d.%Y')}"
