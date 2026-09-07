from datetime import date
from zoneinfo import ZoneInfo

from extensions import db
from models import Assignment, TimesheetEntry, WeeklyPacket, User
from utils import week_bounds
import notifications
import packets


def run_weekly_packet_job(app):
    """Runs every Sunday night: for every active assignment with at least
    one timesheet entry or expense logged this week, build the Verde packet
    PDF and (if Xero is connected) create the matching draft invoice."""
    with app.app_context():
        monday, sunday = week_bounds(date.today())
        assignments = Assignment.query.filter_by(active=True).all()

        for assignment in assignments:
            entries, expenses = packets.week_rows(assignment, monday, sunday)
            if not entries and not expenses:
                continue

            existing_packet = WeeklyPacket.query.filter_by(
                assignment_id=assignment.id, week_start=monday
            ).first()
            if existing_packet:
                continue  # already generated for this week

            packets.create_packet(assignment, monday, sunday)

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
