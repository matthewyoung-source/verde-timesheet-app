from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from extensions import db

RESET_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 2  # reset links are good for 2 hours


class User(UserMixin, db.Model):
    """A person who logs in: role='admin' (Matthew / office staff),
    role='contractor' (a placed contractor submitting time & expenses),
    or role='client' (an end client's contact, read-only approval view --
    see client.py; only meaningful when client_id is set)."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="contractor")  # 'admin' / 'contractor' / 'client'
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Only set for role='client' -- which Client record this login can approve packets for.
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)

    assignments = db.relationship("Assignment", back_populates="contractor", lazy="dynamic")
    client = db.relationship("Client", foreign_keys=[client_id])

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == "admin"

    def is_client(self):
        return self.role == "client"

    def home_endpoint(self):
        if self.is_admin():
            return "admin.dashboard"
        if self.is_client():
            return "client.dashboard"
        return "contractor.dashboard"

    def get_reset_token(self, secret_key):
        """Stateless reset token: signed with the user's id plus a slice of
        their current password hash, so it stops working the moment the
        password actually changes (or once naturally expired)."""
        serializer = URLSafeTimedSerializer(secret_key)
        return serializer.dumps({"uid": self.id, "sig": self.password_hash[-16:]})

    @staticmethod
    def verify_reset_token(token, secret_key):
        serializer = URLSafeTimedSerializer(secret_key)
        try:
            data = serializer.loads(token, max_age=RESET_TOKEN_MAX_AGE_SECONDS)
        except (BadSignature, SignatureExpired):
            return None
        user = db.session.get(User, data.get("uid"))
        if not user or user.password_hash[-16:] != data.get("sig"):
            return None
        return user

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class Client(db.Model):
    """An end client Matthew has placed a contractor with."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    xero_contact_id = db.Column(db.String(100), nullable=True)  # links to Xero Contact once connected
    billing_email = db.Column(db.String(200), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    # Off by default -- Matthew approves hours himself on a Monday call, so
    # Verde's own clients don't need this. Built so the app can be resold to
    # other staffing firms that DO want a client sign-off step recorded.
    requires_client_approval = db.Column(db.Boolean, default=False, nullable=False)

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

    # --- Invoicing detail (matches how Verde bills, e.g. INV176 / INV177) ---
    # Overtime is billed at 1.5x billing_rate unless a specific rate is set here.
    overtime_rate = db.Column(db.Numeric(10, 2), nullable=True)
    # Per diem: what the client is billed per day, and what the contractor
    # receives per day (shown on the timesheet packet). Billed for
    # per_diem_days each week (7 = every day of the week, Verde's default).
    per_diem_bill_rate = db.Column(db.Numeric(10, 2), nullable=True)
    per_diem_contractor_rate = db.Column(db.Numeric(10, 2), nullable=True)
    per_diem_days = db.Column(db.Integer, default=7, nullable=False)
    # Unpaid break deducted from each day's hours when start/end times are used.
    daily_break_hours = db.Column(db.Numeric(4, 2), default=0, nullable=False)
    # PO # printed on the packet (e.g. VerdeCCAbnerBELLSOLAR) and the Xero
    # reference prefix (e.g. VerdeCC_Abner_Bell -> VerdeCC_Abner_Bell_09.06.2026).
    po_number = db.Column(db.String(100), nullable=True)
    invoice_reference_prefix = db.Column(db.String(100), nullable=True)
    start_date = db.Column(db.Date, default=date.today)
    end_date = db.Column(db.Date, nullable=True)  # set when the assignment is ended

    contractor = db.relationship("User", back_populates="assignments")
    client = db.relationship("Client", back_populates="assignments")

    timesheet_entries = db.relationship("TimesheetEntry", back_populates="assignment", lazy="dynamic")
    expenses = db.relationship("Expense", back_populates="assignment", lazy="dynamic")

    def effective_overtime_rate(self):
        if self.overtime_rate is not None:
            return float(self.overtime_rate)
        return round(float(self.billing_rate) * 1.5, 2)

    def __repr__(self):
        return f"<Assignment {self.contractor_id}->{self.client_id} @ {self.billing_rate}/hr>"


class TimesheetEntry(db.Model):
    """One day's worked hours, logged against a specific assignment."""

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Numeric(5, 2), nullable=False)
    # Optional clock times; when both are given, hours is derived from them
    # (less the assignment's daily break) and they print on the packet.
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
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
    # Expense type -- same-type expenses roll up into one line on the Xero
    # invoice (all fuel receipts become a single "Fuel" line, etc).
    category = db.Column(db.String(50), nullable=True)
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
    regular_hours = db.Column(db.Numeric(6, 2), nullable=True)
    overtime_hours = db.Column(db.Numeric(6, 2), nullable=True)
    # Admin can change the per diem day count for one week before approving.
    per_diem_days = db.Column(db.Integer, nullable=True)
    xero_reference = db.Column(db.String(150), nullable=True)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)

    xero_invoice_id = db.Column(db.String(100), nullable=True)
    xero_invoice_status = db.Column(db.String(30), nullable=True)  # 'not_connected'/'draft_created'/'failed'

    # Matthew's own sign-off (his Monday morning call). Once approved, the
    # contractor can no longer edit that week's hours/expenses -- only an admin can.
    approval_status = db.Column(db.String(20), nullable=False, default="pending")  # 'pending'/'approved'
    approved_at = db.Column(db.DateTime, nullable=True)

    # Optional end-client sign-off (see Client.requires_client_approval). Recorded
    # for the audit trail only -- it does not hold up the Xero draft invoice.
    client_approval_status = db.Column(db.String(20), nullable=False, default="not_required")
    # 'not_required' / 'pending' / 'approved' / 'disputed'
    client_approved_at = db.Column(db.DateTime, nullable=True)
    client_approval_note = db.Column(db.String(500), nullable=True)  # e.g. dispute reason

    assignment = db.relationship("Assignment")

    __table_args__ = (
        db.UniqueConstraint("assignment_id", "week_start", name="uq_assignment_week"),
    )

    def is_locked(self):
        return self.approval_status == "approved"


class WeekSubmission(db.Model):
    """A contractor pressing "Submit week": a signal to Matthew that their
    hours and receipts for that week are final. It doesn't change the Sunday
    packet or the Xero draft; it just stops the daily reminders for that week
    and shows as "Submitted" on the admin side."""

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), nullable=False)
    week_start = db.Column(db.Date, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    assignment = db.relationship("Assignment")

    __table_args__ = (
        db.UniqueConstraint("assignment_id", "week_start", name="uq_submission_week"),
    )
