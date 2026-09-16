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
import brief_mail as B

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


# ---------------------------------------------------------------------------
# The same week again as a plain dict, so brief_mail can render the branded HTML
# version. Kept separate from build_brief so a rendering change can never break
# the plain-text brief.
# ---------------------------------------------------------------------------

def _money(v):
    return float(v) if v is not None else None


def _collect(app):
    today = business_today()
    monday, sunday = week_bounds(today)

    pending = (WeeklyPacket.query.filter_by(approval_status="pending")
               .order_by(WeeklyPacket.week_start.desc()).all())

    people = []
    for a in Assignment.query.filter_by(active=True).all():
        entries = TimesheetEntry.query.filter(
            TimesheetEntry.assignment_id == a.id,
            TimesheetEntry.work_date >= monday, TimesheetEntry.work_date <= sunday).all()
        hours = sum(float(e.hours or 0) for e in entries)
        days = sorted({e.work_date for e in entries})
        receipts = Expense.query.filter(Expense.assignment_id == a.id,
                                        Expense.expense_date >= monday,
                                        Expense.expense_date <= sunday).count()
        recent = (TimesheetEntry.query.filter(
            TimesheetEntry.assignment_id == a.id,
            TimesheetEntry.work_date >= today - timedelta(days=21),
            TimesheetEntry.work_date < today)
            .order_by(TimesheetEntry.work_date.desc()).first())
        last = recent.work_date if recent else None
        floor = last or (a.start_date or today) - timedelta(days=1)
        missed = [floor + timedelta(days=i) for i in range(1, (today - floor).days)
                  if (floor + timedelta(days=i)).weekday() < 5]
        flag = ""
        if missed:
            flag = ("not logged since %s, %d weekday%s missing"
                    % (last.strftime("%a %-d"), len(missed), "s" if len(missed) > 1 else "")
                    ) if last else ("nothing logged yet, %d weekday%s missing"
                                    % (len(missed), "s" if len(missed) > 1 else ""))

        bits = [a.role_title] if a.role_title else []
        bill, pay = _money(a.billing_rate), _money(getattr(a, "pay_rate", None))
        if bill is not None:
            bits.append("$%g bill%s" % (bill, " / $%g pay" % pay if pay is not None else ""))
        bits.append("%d day%s logged" % (len(days), "" if len(days) == 1 else "s"))
        bits.append("%d receipt%s" % (receipts, "" if receipts == 1 else "s"))

        people.append({
            "who": "%s, %s" % (a.contractor.name, a.client.name),
            "hours": hours,
            "bill_rate": bill,
            "pay_rate": pay,
            "per_diem_days": int(getattr(a, "per_diem_days", 0) or 0),
            "per_diem_bill": _money(getattr(a, "per_diem_bill_rate", None)) or 0,
            "per_diem_pay": _money(getattr(a, "per_diem_contractor_rate", None)) or 0,
            "fee": float(app.config.get("WEEKLY_PAYMENT_FEE", 30) or 0),
            "note": ", ".join(bits),
            "flag": flag,
        })

    moves = []
    for a in Assignment.query.filter_by(active=True).all():
        if a.start_date and today <= a.start_date <= today + timedelta(days=7):
            moves.append({"who": "%s at %s" % (a.contractor.name, a.client.name),
                          "when": a.start_date.strftime("%a %-d"), "what": "starts", "kind": "start"})
        if a.end_date and today <= a.end_date <= today + timedelta(days=7):
            moves.append({"who": "%s at %s" % (a.contractor.name, a.client.name),
                          "when": a.end_date.strftime("%a %-d"), "what": "ends", "kind": "end"})

    jobs = []
    for job in ("weekly_packets", "daily_reminder"):
        r = (JobRun.query.filter(JobRun.job == job, JobRun.ok.isnot(None))
             .order_by(JobRun.started_at.desc()).first())
        if r:
            jobs.append({"label": "%s, %s %s UTC" % (job.replace("_", " ").capitalize(),
                                                     "ok at" if r.ok else "FAILED at",
                                                     r.finished_at.strftime("%b %-d %H:%M")),
                         "ok": bool(r.ok)})
        else:
            jobs.append({"label": "%s, never run" % job.replace("_", " ").capitalize(), "ok": False})

    behind = [p for p in people if p["flag"]]
    if pending:
        headline = "%d packet%s waiting for your approval." % (len(pending), "" if len(pending) == 1 else "s")
    elif behind:
        headline = "%d contractor%s behind on hours." % (len(behind), "" if len(behind) == 1 else "s")
    elif people:
        headline = "%d on site, everyone up to date." % len(people)
    else:
        headline = "Nobody on site this week."

    return {
        "date_label": today.strftime("%A, %-d %B %Y"),
        "headline": headline,
        "week_label": "%s to %s" % (monday.strftime("%b %-d"), sunday.strftime("%b %-d")),
        "pending_count": len(pending),
        "pending": [{"who": "%s, %s" % (p.assignment.contractor.name, p.assignment.client.name),
                     "invoice": float(p.invoice_total or 0),
                     "week": p.week_start.strftime("%b %-d"),
                     "hours": float(p.total_hours or 0),
                     "expenses": float(p.total_expenses or 0),
                     "xero": p.xero_invoice_status or "not created"} for p in pending],
        "people": people,
        "moves": moves,
        "jobs": jobs,
    }


def _totals(p):
    """What one contractor invoices and earns this week: the hourly spread, the
    per diem spread, and the flat weekly payment fee."""
    hours = p.get("hours") or 0
    bill = hours * p["bill_rate"] if p.get("bill_rate") is not None else None
    pay = hours * p["pay_rate"] if p.get("pay_rate") is not None else None
    pd_days = p.get("per_diem_days") or 0
    pd_bill = pd_days * (p.get("per_diem_bill") or 0)
    pd_pay = pd_days * (p.get("per_diem_pay") or 0)
    fee = p.get("fee") or 0
    invoiced = (bill or 0) + pd_bill
    margin = None if pay is None else (bill or 0) - pay + (pd_bill - pd_pay) + fee
    return {"hours_bill": bill, "hours_pay": pay, "pd_days": pd_days, "pd_bill": pd_bill,
            "pd_pay": pd_pay, "fee": fee, "invoiced": invoiced, "margin": margin}


def _ts_html(d):
    people = d["people"]
    t = {id(p): _totals(p) for p in people}
    logged = sum(p["hours"] for p in people)
    invoiced = sum(t[id(p)]["invoiced"] for p in people)
    unpriced = [p for p in people if t[id(p)]["margin"] is None and p["hours"]]
    margin = sum(t[id(p)]["margin"] or 0 for p in people)
    if unpriced:
        m_val = "not set"
        m_note = "no pay rate on %s" % ", ".join(p["who"].split(",")[0] for p in unpriced)
        m_tone = "warn"
    else:
        m_val = B.money(margin)
        m_note = (("%.0f%% of what you invoiced, before comp and factoring"
                   % (100 * margin / invoiced)) if invoiced else "before comp and factoring")
        m_tone = "good" if margin else ""

    inv_note = []
    h = sum(t[id(p)]["hours_bill"] or 0 for p in people)
    pdb = sum(t[id(p)]["pd_bill"] for p in people)
    if h:
        inv_note.append("%s hours" % B.money(h))
    if pdb:
        inv_note.append("%s per diem" % B.money(pdb))

    blocks = [B.kpis([
        ("Packets waiting", str(d["pending_count"]),
         "need approving" if d["pending_count"] else "all clear",
         "warn" if d["pending_count"] else "good"),
        ("On site", str(len(people)), "%g hours this week" % logged, ""),
        ("Invoiced this week", B.money(invoiced), ", ".join(inv_note) or d["week_label"], ""),
        ("Gross margin", m_val, m_note, m_tone),
    ])]

    if d["pending"]:
        blocks.append(B.section("Waiting for your approval"))
        blocks.append(B.rows([(p["who"], B.money(p["invoice"]),
                               "week of %s, %g hours, expenses %s, Xero %s"
                               % (p["week"], p["hours"], B.money(p["expenses"]), p["xero"]), "warn")
                              for p in d["pending"]]))

    blocks.append(B.section("Hours logged this week",
                            d["week_label"] + ". Bar is against a 40 hour week."))
    if people:
        rows = []
        for p in people:
            tt = t[id(p)]
            note = p["flag"] or p["note"]
            if not p["flag"] and tt["invoiced"]:
                note += ". invoiced %s, margin %s" % (
                    B.money(tt["invoiced"]),
                    B.money(tt["margin"]) if tt["margin"] is not None
                    else "not calculated (no pay rate set)")
            rows.append((p["who"], "%g" % p["hours"], min(1.0, p["hours"] / 40.0), note,
                         "warn" if p["flag"] else ("good" if p["hours"] else "")))
        blocks.append(B.bars(rows, suffix=" hrs"))
    else:
        blocks.append(B.empty("No active assignments."))

    if not unpriced and invoiced:
        lines = []
        hp = sum(t[id(p)]["hours_pay"] or 0 for p in people)
        pdp = sum(t[id(p)]["pd_pay"] for p in people)
        pdd = sum(t[id(p)]["pd_days"] for p in people)
        fees = sum(t[id(p)]["fee"] for p in people)
        if h:
            lines.append(("Hourly spread", B.money(h - hp),
                          "%g hours, %s billed against %s paid" % (logged, B.money(h), B.money(hp)),
                          "good"))
        if pdb:
            lines.append(("Per diem", B.money(pdb - pdp),
                          "%g day%s, %s billed against %s paid"
                          % (pdd, "" if pdd == 1 else "s", B.money(pdb), B.money(pdp)), "good"))
        if fees:
            lines.append(("Payment fee", B.money(fees), "flat, per contractor per week", "good"))
        lines.append(("Gross margin", m_val,
                      "%s invoiced. Workers comp and factoring come out of this." % B.money(invoiced),
                      m_tone))
        blocks.append(B.section("Where the margin comes from", "This week to date, not a full week."))
        blocks.append(B.rows(lines))

    if d["moves"]:
        blocks.append(B.section("Starts and ends this week"))
        blocks.append(B.rows([(m["who"], m["when"], m["what"],
                               "good" if m["kind"] == "start" else "warn") for m in d["moves"]]))

    blocks.append(B.section("Jobs"))
    blocks.append(B.pills([(j["label"], "good" if j["ok"] else "bad") for j in d["jobs"]]))

    return B.page("Timesheets", d["date_label"], d["headline"], blocks,
                  "Sent by the timesheet app each morning. Packets build Sunday night and the Xero "
                  "invoice follows your approval.")


def run_morning_brief(app):
    with app.app_context():
        try:
            to = _brief_address(app)
            if not to:
                logger.info("Morning brief: no address, skipped.")
                return
            body = build_brief(app)
            try:
                html = _ts_html(_collect(app))
            except Exception:
                # A rendering problem must never stop the brief going out.
                logger.exception("Morning brief: HTML render failed, sending plain text only")
                html = None
            _send(app, to, f"[Verde brief] Timesheets, {date.today():%a %-d %b}", body, html)
            logger.info("Morning brief sent to %s", to)
        except Exception:
            logger.exception("Morning brief failed")


def _send(app, to_address, subject, body_text, body_html=None):
    """Sends the brief directly rather than through notifications.send_email, so
    the HTML alternative can be attached without changing a module the rest of
    the app relies on. Falls back to notifications.send_email if SMTP is not
    configured here, so behaviour is unchanged when email is off."""
    import smtplib
    from email.message import EmailMessage
    if not body_html or not app.config.get("SMTP_HOST"):
        return notifications.send_email(app, to_address, subject, body_text)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = app.config["MAIL_FROM"]
    msg["To"] = to_address
    # Styled version only. The plain text is still built and is used as the
    # fallback above if the HTML render fails, because no brief at all would be
    # worse than a plain one.
    msg.set_content(body_html, subtype="html")
    try:
        with smtplib.SMTP(app.config["SMTP_HOST"], app.config["SMTP_PORT"], timeout=20) as server:
            if app.config.get("SMTP_USE_TLS", True):
                server.starttls()
            if app.config.get("SMTP_USERNAME"):
                server.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send the brief to %s", to_address)
        return False


def register_brief(app, scheduler):
    raw = (os.environ.get("BRIEF_UTC_TIME") or "06:30").strip()
    try:
        hour, minute = [int(x) for x in raw.split(":")]
    except ValueError:
        hour, minute = 6, 30
    scheduler.add_job(func=lambda: run_morning_brief(app), trigger="cron", hour=hour, minute=minute,
                      timezone="UTC", id="morning_brief", replace_existing=True)
