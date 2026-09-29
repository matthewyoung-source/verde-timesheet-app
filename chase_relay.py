"""Sends a report to Matthew from Chase Sullivan's mailbox through the Make
scenario "Chase report sender (apps, webhook)".

Switched on only when both CHASE_RELAY_URL and CHASE_RELAY_KEY are set in the
environment. The Make scenario ignores the recipient entirely: it only ever
sends to matthew.young@verdesolutions.co.uk, with Reply-To set to the same
address, and only when the key matches.

Returns True when Make accepted the request, False otherwise, so the caller
can fall back to its normal SMTP route and a report is never lost.
"""

import html as _html
import logging
import os

import requests

logger = logging.getLogger(__name__)


def enabled():
    return bool(os.environ.get("CHASE_RELAY_URL") and os.environ.get("CHASE_RELAY_KEY"))


def send(subject, body_html=None, body_text=None):
    if not enabled():
        return False
    if not body_html:
        body_html = ("<pre style=\"font-family:Arial,Helvetica,sans-serif;white-space:pre-wrap\">"
                     + _html.escape(body_text or "") + "</pre>")
    try:
        r = requests.post(
            os.environ["CHASE_RELAY_URL"].strip(),
            json={"key": os.environ["CHASE_RELAY_KEY"].strip(), "subject": subject, "html": body_html},
            timeout=20,
        )
        if r.status_code == 200:
            logger.info("Report relayed through Chase: %s", subject)
            return True
        logger.error("Chase relay refused %s: HTTP %s %s", subject, r.status_code, r.text[:200])
    except Exception:
        logger.exception("Chase relay failed for %s", subject)
    return False
