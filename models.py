from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db


class User(UserMixin, db.Model):
    """A person who logs in: either role='admin' (Matthew / office staff)
    or role='contractor' (a placed contractor submitting time & expenses)."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="contractor")  # 'admin' or 'contractor'
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    assignments = db.relationship("Assignment", back_populates="contractor", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == "admin"

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class Client(db.Model):
    """An end client Matthew has placed a contractor with."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    xero_contact_id = db.Column(db.String(100), nullable=True)  # links to Xero Contact once connected
    billing_email = db.Column(db.String(200), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    assignments = db.relationship("Assignment", back_populates="client", lazy="dynamic")

    def __repr__(self):
        return f"<Client {self.name}>"


class Assignment(db.Model):
    """Ties a contractor to a client at a specific billing rate.
    A contractor can have more than one active assignment (e.g. two clients)."""

    id = db.Column(db.Integer, primary_key=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    billing_rate = db.Column(db.Numeric(10, 2), nullable=False)  # $/hour charged to client
    role_title = db.Column(db.String(150), nullable=True)  # e.g. "Site Electrician"
    active = db.Column(db.Boolean, default=True, nullable=False)
    start_date = db.Column(db.Date, default=date.today)

    contractor = db.relationship("User", back_populates="assignments")
    client = db.relationship("Client", back_populates="assignments")

    timesheet_entries = db.relationship("TimesheetEntry", back_populates="assignment", lazy="dynamic")
    expenses = db.relationship("Expense", back_populates="assignment", lazy="dynamic")

    def __repr__(self):
        return f"<Assignment {self.contractor_id}->{self.client_id} @ {self.billing_rate}/hr>"


class TimesheetEntry(db.Model):
    """One day's worked hours, logged against a specific assignment."""

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Numeric(5, 2), nullable=False)
    notes = db.Column(db.String(300), nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    assignment = db.relationship("Assignment", back_populates="timesheet_entries")

    __table_args__ = (
        db.UniqueConstraint("assignment_id", "work_date", name="uq_assignment_date"),
    )


class Expense(db.Model):
    """A receipt photo plus its dollar amount. amount is what counts for
    reimbursement records; ocr_amount is what auto-extraction guessed
    (kept for reference / audit), and is_amount_confirmed tells the admin
    whether the contractor has checked the auto-read figure."""

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), nullable=False)
    expense_date = db.Column(db.Date, nullable=False)
    photo_filename = db.Column(db.String(300), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=True)  # confirmed amount
    ocr_amount = db.Column(db.Numeric(10, 2), nullable=True)  # raw auto-read guess
    ocr_confidence = db.Column(db.String(20), nullable=True)  # 'high' / 'low' / 'none'
    is_amount_confirmed = db.Column(db.Boolean, default=False, nullable=False)
    description = db.Column(db.String(300), nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    assignment = db.relationship("Assignment", back_populates="expenses")


class XeroToken(db.Model):
    """Single-row table holding the current Xero OAuth2 tokens, once Matthew
    connects his Xero organisation from the admin panel. Until this row
    exists, invoice creation is skipped and PDFs are still generated as normal."""

    id = db.Column(db.Integer, primary_key=True)
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    tenant_id = db.Column(db.String(100), nullable=False)
    connected_at = db.Column(db.DateTime, default=datetime.utcnow)


class WeeklyPacket(db.Model):
    """Record of a generated Sunday PDF (and, if Xero is connected, the
    resulting draft invoice) for one contractor for one client for one week."""

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), nullable=False)
    week_start = db.Column(db.Date, nullable=False)
    week_end = db.Column(db.Date, nullable=False)
    pdf_filename = db.Column(db.String(300), nullable=True)
    total_hours = db.Column(db.Numeric(6, 2), nullable=True)
    total_expenses = db.Column(db.Numeric(10, 2), nullable=True)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)

    xero_invoice_id = db.Column(db.String(100), nullable=True)
    xero_invoice_status = db.Column(db.String(30), nullable=True)  # 'not_connected'/'draft_created'/'failed'

    assignment = db.relationship("Assignment")

    __table_args__ = (
        db.UniqueConstraint("assignment_id", "week_start", name="uq_assignment_week"),
    )
