import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from extensions import db
from models import Assignment, TimesheetEntry, WeeklyPacket, User, WeekSubmission, JobRun

logger = logging.getLogger(__name__)
from utils import week_bounds, business_today, to_local
import notifications
import packets


def run_weekly_packet_job(app):
    """Runs every Sunday night: for every active assignment with at least
    one timesheet entry or expense logged this week, build the Verde packet
    PDF and (if Xero is connected) create the matching draft invoice."""
    with app.app_context():
        monday, sunday = week_bounds(business_today())
        assignments = Assignment.query.filter_by(active=True).all()
        built, skipped, problems = 0, 0, []

        for assignment in assignments:
            label = f"{assignment.contractor.name} / {assignment.client.name}"
            try:
                entries, expenses = packets.week_rows(assignment, monday, sunday)
                if not entries and not expenses:
                    skipped += 1
                    continue

                existing_packet = WeeklyPacket.query.filter_by(
                    assignment_id=assignment.id, week_start=monday
                ).first()
                if existing_packet:
                    continue  # already generated for this week

                packet = packets.create_packet(assignment, monday, sunday)
                db.session.commit()
                built += 1
                if packet is not None and packet.xero_invoice_status == "failed":
                    problems.append(f"{label}: packet built but the Xero draft invoice failed")
            except Exception as exc:
                # One bad assignment must never stop the others' packets.
                db.session.rollback()
                logger.exception("Packet failed for %s", label)
                problems.append(f"{label}: {type(exc).__name__}: {exc}")

        summary = f"{built} packet{'s' if built != 1 else ''} built for week of {monday:%b %-d}, {skipped} assignment{'s' if skipped != 1 else ''} with nothing logged."
        if problems:
            raise JobProblem(f"{len(problems)} problem(s). " + " | ".join(problems) + f" ({summary})")
        return summary


class JobProblem(Exception):
    """Raised at the end of a job that finished but hit failures on the way."""


def run_daily_reminder_job(app):
    """Runs every day at 5pm Pacific (configurable): emails any contractor
    with an active assignment who hasn't logged an hours entry for today yet."""
    with app.app_context():
        if not notifications.is_configured(app):
            return "Email isn't configured, so no reminders were sent."

        today = business_today()
        monday, _ = week_bounds(today)
        contractors = User.query.filter_by(role="contractor", active=True).all()
        sent = 0

        for contractor in contractors:
            active = contractor.assignments.filter_by(active=True).all()
            if not active:
                continue

            # Pressed "Submit week" already? Then they're done: no nagging.
            submitted_ids = {
                s.assignment_id
                for s in WeekSubmission.query.filter(
                    WeekSubmission.week_start == monday,
                    WeekSubmission.assignment_id.in_([a.id for a in active]),
                ).all()
            }
            if all(a.id in submitted_ids for a in active):
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
            sent += 1
        return f"Reminded {sent} contractor{'s' if sent != 1 else ''} for {today:%a %b %-d}."


JOB_LABELS = {"weekly_packets": "Sunday packet run", "daily_reminder": "Daily reminder"}


def alert_address(app):
    configured = (app.config.get("ALERT_EMAIL") or "").strip()
    if configured:
        return configured
    admin = User.query.filter_by(role="admin", active=True).order_by(User.id).first()
    return admin.email if admin else None


class RunResult:
    def __init__(self, run):
        self.id, self.ok, self.message = run.id, run.ok, run.message
        self.started_at, self.finished_at = run.started_at, run.finished_at


def run_guarded(app, job_name, fn, source="scheduled"):
    """Runs a job, records the outcome as a JobRun row, and emails the owner
    when a scheduled run starts failing (once, not every time) and again
    when it recovers."""
    with app.app_context():
        previous = JobRun.latest_finished(job_name)
        run = JobRun(job=job_name, source=source, started_at=datetime.utcnow())
        db.session.add(run)
        db.session.commit()
        run_id = run.id
        try:
            message = fn()
            ok, message = True, (message or "Completed.")
        except Exception as exc:
            db.session.rollback()
            logger.exception("%s failed", job_name)
            ok = False
            message = str(exc) if isinstance(exc, JobProblem) else f"{type(exc).__name__}: {exc}"

        run = db.session.get(JobRun, run_id)
        run.ok = ok
        run.message = str(message)[:500]
        run.finished_at = datetime.utcnow()
        db.session.commit()

        if source == "scheduled":
            to = alert_address(app)
            label = JOB_LABELS.get(job_name, job_name)
            base = app.config.get("APP_BASE_URL", "").rstrip("/")
            when = to_local(run.finished_at).strftime("%a %b %-d, %-I:%M %p")
            if to and not ok and (previous is None or previous.ok):
                notifications.send_email(
                    app, to, f"[Verde timesheets] {label} hit a problem",
                    f"The {label} had a problem at {when}:\n\n{run.message}\n\n"
                    f"Anything that did work has been saved. Check {base}/admin/ for the details. "
                    "You'll get one more email when it runs clean again.",
                )
            elif to and ok and previous is not None and previous.ok is False:
                notifications.send_email(
                    app, to, f"[Verde timesheets] {label} is back to normal",
                    f"The {label} ran clean at {when}.\n\n{run.message}",
                )
        return RunResult(run)


def run_packets(app, source="scheduled"):
    return run_guarded(app, "weekly_packets", lambda: run_weekly_packet_job(app), source=source)


def run_reminders(app, source="scheduled"):
    return run_guarded(app, "daily_reminder", lambda: run_daily_reminder_job(app), source=source)


def register_jobs(app, scheduler):
    # Sunday at 11:50 PM Pacific -- captures the whole Monday-Sunday work
    # week, including any hours logged later that same Sunday. Pinned to the
    # business timezone; the server clock is UTC.
    scheduler.add_job(
        func=lambda: run_packets(app),
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=50,
        timezone=ZoneInfo(app.config["BUSINESS_TIMEZONE"]),
        id="weekly_packet_job",
        replace_existing=True,
    )

    # Daily nudge (5pm Pacific by default) to log today's time and expenses.
    scheduler.add_job(
        func=lambda: run_reminders(app),
        trigger="cron",
        hour=app.config["REMINDER_HOUR"],
        minute=app.config["REMINDER_MINUTE"],
        timezone=ZoneInfo(app.config["REMINDER_TIMEZONE"]),
        id="daily_reminder_job",
        replace_existing=True,
    )
