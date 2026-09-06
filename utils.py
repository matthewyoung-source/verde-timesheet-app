from datetime import date, timedelta


def week_bounds(reference_date=None):
    """Returns (monday, sunday) for the work week containing reference_date.
    Work weeks run Monday through Sunday; the Sunday PDF job captures the
    week that just finished, including that same Sunday."""
    reference_date = reference_date or date.today()
    monday = reference_date - timedelta(days=reference_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def previous_week_bounds(reference_date=None):
    monday, _ = week_bounds(reference_date)
    prev_monday = monday - timedelta(days=7)
    prev_sunday = prev_monday + timedelta(days=6)
    return prev_monday, prev_sunday
