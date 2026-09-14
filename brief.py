"""Morning brief email from the timesheet app: packets waiting for approval,
who has and has not logged hours this week, week-to-date billing, and any
job failures. Sent once a day at BRIEF_UTC_TIME (HH:MM, UTC; default
06:30) to BRIEF_EMAIL, else ALERT_EMAIL, else the first admin. Read by the
scheduled morning-brief job in Cowork."""

import logging
import os
from datetime import date, datetime, timedelta

from models import Assignment, TimesheetEntry, Expense, WeeklyPacket, WeekSubmission, User, JobRun
from utils import week_bounds, business_today
import notifications

logger = logging.getLogger(__name__)


def _brief_address(app):
    for key in ("BRIEF_EMAIL", "ALERT_EMAIL"):
        v = (os.environ.get(key) or app.config.get(key) or "").strip()
        if v:
            return v
    admin = User.query.filter_by(role="admin", active=True).order_by(User.id).first()
    return admin.email if admin else None


def build_brief(app):
    today = business_today()
    monday, sunday = week_bounds(today)
    out = []

    pending = (WeeklyPacket.query.filter_by(approval_status="pending")
               .order_by(WeeklyPacket.week_start.desc()).all())
    out.append(f"== PACKETS WAITING FOR APPROVAL: {len(pending)} ==")
    for p in pending:
        a = p.assignment
        out.append(f"- {a.contractor.name} at {a.client.name}, week of {p.week_start:%b %-d}: {float(p.total_hours or 0):g} hours, "
                   f"expenses ${float(p.total_expenses or 0):,.2f}, invoice ${float(p.invoice_total or 0):,.2f}, Xero: {p.xero_invoice_status or 'not created'}")
    out.append("")

    out.append(f"== THIS WEEK, {monday:%b %-d} to {sunday:%b %-d} (business day {today:%A}) ==")
    active = Assignment.query.filter_by(active=True).all()
    if not active:
        out.append("- no active assignments")
    for a in active:
        entries = TimesheetEntry.query.filter(TimesheetEntry.assignment_id == a.id, TimesheetEntry.work_date >= monday,
                                              TimesheetEntry.work_date <= sunday).all()
        hours = sum(float(e.hours or 0) for e in entries)
        days = sorted({e.work_date for e in entries})
        recent = (TimesheetEntry.query.filter(TimesheetEntry.assignment_id == a.id, TimesheetEntry.work_date >= today - timedelta(days=21),
                                              TimesheetEntry.work_date < today).order_by(TimesheetEntry.work_date.desc()).first())
        last = recent.work_date if recent else None
        expenses = Expense.query.filter(Expense.assignment_id == a.id, Expense.expense_date >= monday,
                                        Expense.expense_date <= sunday).count()
        submitted = WeekSubmission.query.filter_by(assignment_id=a.id, week_start=monday).first()
        # Weekdays before today that have nothing logged since the last entry.
        floor = last or (a.start_date or today) - timedelta(days=1)
        missed = [floor + timedelta(days=i) for i in range(1, (today - floor).days) if (floor + timedelta(days=i)).weekday() < 5]
        flag = ""
        if missed:
            flag = "  <-- NOT LOGGED " + (f"since {last:%a %-d} ({len(missed)} weekday{'s' if len(missed) > 1 else ''} missing)" if last else f"at all ({len(missed)} weekday{'s' if len(missed) > 1 else ''} missing)")
        out.append(f"- {a.contractor.name} at {a.client.name}{' (' + a.role_title + ')' if a.role_title else ''}: {hours:g} hours over {len(days)} days, "
                   f"{expenses} receipts, last logged {last:%a %-d} " if last else f"- {a.contractor.name} at {a.client.name}{' (' + a.role_title + ')' if a.role_title else ''}: 0 hours, {expenses} receipts, nothing logged yet ")
        out[-1] += ("[week submitted]" if submitted else "") + flag
        if a.billing_rate and hours:
            out[-1] += f"; week to date billing about ${hours * float(a.billing_rate):,.0f}"
    out.append("")

    starts = [a for a in Assignment.query.filter_by(active=True).all() if a.start_date and today <= a.start_date <= today + timedelta(days=7)]
    ends = [a for a in Assignment.query.filter_by(active=True).all() if a.end_date and today <= a.end_date <= today + timedelta(days=7)]
    if starts or ends:
        out.append("== STARTS AND ENDS this week ==")
        for a in starts:
            out.append(f"- starts {a.start_date:%a %-d}: {a.contractor.name} at {a.client.name}")
        for a in ends:
            out.append(f"- ends {a.end_date:%a %-d}: {a.contractor.name} at {a.client.name}")
        out.append("")

    out.append("== JOBS ==")
    for job in ("weekly_packets", "daily_reminder"):
        r = JobRun.query.filter(JobRun.job == job, JobRun.ok.isnot(None)).order_by(JobRun.started_at.desc()).first()
        out.append(f"- {job}: " + (f"{'ok' if r.ok else 'FAILED'} at {r.finished_at:%b %-d %H:%M} UTC, {r.message}" if r else "never run"))
    return "\n".join(out)


def run_morning_brief(app):
    with app.app_context():
        try:
            to = _brief_address(app)
            if not to:
                logger.info("Morning brief: no address, skipped.")
                return
            body = build_brief(app)
            notifications.send_email(app, to, f"[Verde brief] Timesheets, {date.today():%a %-d %b}", body)
            logger.info("Morning brief sent to %s", to)
        except Exception:
            logger.exception("Morning brief failed")


def register_brief(app, scheduler):
    raw = (os.environ.get("BRIEF_UTC_TIME") or "06:30").strip()
    try:
        hour, minute = [int(x) for x in raw.split(":")]
    except ValueError:
        hour, minute = 6, 30
    scheduler.add_job(func=lambda: run_morning_brief(app), trigger="cron", hour=hour, minute=minute,
                      timezone="UTC", id="morning_brief", replace_existing=True)
