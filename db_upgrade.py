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
]


def run(db):
    for statement in STATEMENTS:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()  # column already exists (or db doesn't need it) -- fine
