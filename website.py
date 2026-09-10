"""The public Verde Contracts website (verdecontracts.com), served by the
timesheet app itself so it needs no extra hosting and the waitlist form
has somewhere to send email from."""
import logging
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, current_app, abort

from extensions import csrf, limiter
import notifications

logger = logging.getLogger(__name__)

site_bp = Blueprint("site", __name__)

SITE_HOSTS = {"verdecontracts.com", "www.verdecontracts.com"}


def is_site_host():
    return request.host.split(":")[0].lower() in SITE_HOSTS


def _ctx():
    return {"year": datetime.utcnow().year}


def render_site():
    return render_template("site_contracts.html", **_ctx())


@site_bp.before_app_request
def canonical_host():
    host = request.host.split(":")[0].lower()
    if host == "www.verdecontracts.com":
        return redirect("https://verdecontracts.com" + request.full_path.rstrip("?"), code=301)


@site_bp.route("/welcome")
def preview():
    return render_site()


@site_bp.route("/inquiry", methods=["POST"])
@csrf.exempt
@limiter.limit("5 per minute; 20 per day")
def inquiry():
    if request.form.get("website_url"):  # honeypot
        return redirect(url_for("site.thanks"))
    name = request.form.get("name", "").strip()[:120]
    company = request.form.get("company", "").strip()[:160]
    email = request.form.get("email", "").strip()[:200]
    size = request.form.get("size", "").strip()[:40]
    message = request.form.get("message", "").strip()[:2000]
    if not name or not email or "@" not in email:
        abort(400)

    from scheduler_jobs import alert_address
    app = current_app._get_current_object()
    to = alert_address(app)
    body = (
        "New waitlist signup from the Verde Contracts website.\n\n"
        f"Name: {name}\nFirm: {company}\nEmail: {email}\nContractors on assignment: {size or '?'}\n\n"
        f"How timesheets work for them today:\n{message or '(blank)'}\n\n"
        f"Reply to them directly: {email}"
    )
    if to:
        notifications.send_email(app, to, f"[Verde Contracts] Waitlist: {name} at {company or 'unknown firm'} ({size})", body)
    first = name.split(" ")[0]
    notifications.send_email(
        app, email, f"You're on the Verde Contracts waitlist, {first}",
        f"Hi {first},\n\nThanks for joining the Verde Contracts waitlist. You'll get one email from me when "
        f"there's a version for other firms to try, and waitlist members get the launch price.\n\n"
        f"If you want to talk sooner, just reply to this email.\n\nMatthew Young\nVerde Contracts\nScottsdale, Arizona",
    )
    logger.info("Waitlist signup from %s <%s> (%s, %s)", name, email, company, size)
    return redirect(url_for("site.thanks"))


@site_bp.route("/thanks")
def thanks():
    return render_template("site_thanks.html", **_ctx())
