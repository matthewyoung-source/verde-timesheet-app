"""Best-effort auto-read of a dollar amount off a receipt photo.

Uses local Tesseract OCR (no external API, no account needed) to pull text
off the image, then looks for the figure that is most likely the total.
This is a first pass, not a guarantee -- the contractor always sees the
guess and can correct it before submitting, and the admin can see whether
it was auto-read or confirmed.
"""

import re
from decimal import Decimal, InvalidOperation

from PIL import Image
import pytesseract

# Words that usually sit right next to the figure we actually want.
TOTAL_KEYWORDS = ["total", "amount due", "balance due", "grand total", "amount"]

MONEY_PATTERN = re.compile(r"\$?\s?(\d{1,5}(?:,\d{3})*(?:\.\d{2})?)")


def _to_decimal(raw):
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def extract_amount_from_receipt(image_path):
    """Returns (amount_or_None, confidence) where confidence is
    'high' (found next to a total-like keyword), 'low' (largest number
    found on the receipt, best guess), or 'none' (couldn't read anything)."""
    try:
        image = Image.open(image_path)
        # PSM 6 ("assume a single uniform block of text") keeps each
        # printed line intact -- receipts otherwise sometimes get split
        # into a "labels" column and a "prices" column, which breaks
        # same-line keyword matching below.
        text = pytesseract.image_to_string(image, config="--psm 6")
    except Exception:
        return None, "none"

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Pass 1: look for a line containing a total-style keyword with a
    # dollar figure on the same line -- most reliable signal. Scan from
    # the bottom up, since the grand total is usually the last such line
    # on a receipt (subtotal/tax lines come before it), and explicitly
    # skip "subtotal" lines (which otherwise match "total" as a substring).
    for line in reversed(lines):
        lower = line.lower()
        if "subtotal" in lower or "sub total" in lower:
            continue
        if any(keyword in lower for keyword in TOTAL_KEYWORDS):
            match = MONEY_PATTERN.search(line)
            if match:
                amount = _to_decimal(match.group(1))
                if amount is not None:
                    return amount, "high"

    # Pass 2: no labeled total found -- fall back to the largest dollar
    # figure anywhere on the receipt (usually the total is the biggest
    # number on a simple receipt).
    candidates = []
    for line in lines:
        for match in MONEY_PATTERN.finditer(line):
            amount = _to_decimal(match.group(1))
            if amount is not None:
                candidates.append(amount)

    if candidates:
        return max(candidates), "low"

    return None, "none"
