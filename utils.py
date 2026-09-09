import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def business_tz():
    """Pacific by default (BUSINESS_TIMEZONE). Read from the environment so
    it works inside background jobs and scripts, not just web requests."""
    return ZoneInfo(os.environ.get("BUSINESS_TIMEZONE", "America/Los_Angeles"))


def business_now():
    """Current time in the business timezone (aware)."""
    return datetime.now(business_tz())


def business_today():
    """Today's date in the business timezone. Use this instead of
    date.today(): the server runs on UTC, so from 4/5pm Pacific onwards
    date.today() would already say tomorrow."""
    return business_now().date()


def to_local(value):
    """Naive UTC datetime from the database -> aware business-timezone datetime."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(business_tz())


def week_bounds(reference_date=None):
    """Returns (monday, sunday) for the work week containing reference_date.
    Work weeks run Monday through Sunday; the Sunday PDF job captures the
    week that just finished, including that same Sunday."""
    reference_date = reference_date or business_today()
    monday = reference_date - timedelta(days=reference_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def previous_week_bounds(reference_date=None):
    monday, _ = week_bounds(reference_date)
    prev_monday = monday - timedelta(days=7)
    prev_sunday = prev_monday + timedelta(days=6)
    return prev_monday, prev_sunday
