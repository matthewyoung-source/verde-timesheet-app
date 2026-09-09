"""Tiny hand-rolled schema upgrader.

This app has no Alembic/Flask-Migrate set up, and db.create_all() (called in
app.py on every startup) only creates tables that don't exist yet -- it never
adds a new column to a table that's already there. So when a new column gets
added to a model, it needs a one-line ADD COLUMN statement here too.

Each statement is tried independently and any error (most commonly "column
already exists", which Postgres and SQLite phrase differently) is swallowed --
that keeps this safe to run on every single startup, on a brand new database
or one that's already been upgraded.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger("db_upgrade")

STATEMENTS = [
    'ALTER TABLE "user" ADD COLUMN client_id INTEGER',
    "ALTER TABLE assignment ADD COLUMN end_date DATE",
    "ALTER TABLE client ADD COLUMN requires_client_approval BOOLEAN DEFAULT FALSE NOT NULL",
    "ALTER TABLE weekly_packet ADD COLUMN approval_status VARCHAR(20) DEFAULT 'pending' NOT NULL",
    "ALTER TABLE weekly_packet ADD COLUMN approved_at TIMESTAMP",
    "ALTER TABLE weekly_packet ADD COLUMN client_approval_status VARCHAR(20) DEFAULT 'not_required' NOT NULL",
    "ALTER TABLE weekly_packet ADD COLUMN client_approved_at TIMESTAMP",
    "ALTER TABLE weekly_packet ADD COLUMN client_approval_note VARCHAR(500)",
    # Invoicing detail (Sep 2026): per diem, overtime, PO / reference, clock times.
    "ALTER TABLE assignment ADD COLUMN overtime_rate NUMERIC(10, 2)",
    "ALTER TABLE assignment ADD COLUMN per_diem_bill_rate NUMERIC(10, 2)",
    "ALTER TABLE assignment ADD COLUMN per_diem_contractor_rate NUMERIC(10, 2)",
    "ALTER TABLE assignment ADD COLUMN per_diem_days INTEGER DEFAULT 7 NOT NULL",
    "ALTER TABLE assignment ADD COLUMN daily_break_hours NUMERIC(4, 2) DEFAULT 0 NOT NULL",
    "ALTER TABLE assignment ADD COLUMN po_number VARCHAR(100)",
    "ALTER TABLE assignment ADD COLUMN invoice_reference_prefix VARCHAR(100)",
    "ALTER TABLE timesheet_entry ADD COLUMN start_time TIME",
    "ALTER TABLE timesheet_entry ADD COLUMN end_time TIME",
    "ALTER TABLE expense ADD COLUMN category VARCHAR(50)",
    # Pay rate and margin tracking (Sep 2026).
    "ALTER TABLE assignment ADD COLUMN pay_rate NUMERIC(10, 2)",
    "ALTER TABLE assignment ADD COLUMN overtime_pay_rate NUMERIC(10, 2)",
    "ALTER TABLE assignment ADD COLUMN expense_markup NUMERIC(10, 2) DEFAULT 0",
    "ALTER TABLE expense ADD COLUMN billed_amount NUMERIC(10, 2)",
    "ALTER TABLE weekly_packet ADD COLUMN invoice_total NUMERIC(10, 2)",
    "ALTER TABLE weekly_packet ADD COLUMN contractor_cost NUMERIC(10, 2)",
    "ALTER TABLE weekly_packet ADD COLUMN regular_hours NUMERIC(6, 2)",
    "ALTER TABLE weekly_packet ADD COLUMN overtime_hours NUMERIC(6, 2)",
    "ALTER TABLE weekly_packet ADD COLUMN per_diem_days INTEGER",
    "ALTER TABLE weekly_packet ADD COLUMN xero_reference VARCHAR(150)",
]


def run(db):
    for statement in STATEMENTS:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()  # column already exists (or db doesn't need it) -- fine
