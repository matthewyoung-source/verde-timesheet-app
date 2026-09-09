import os
from flask import Flask, send_from_directory, abort, redirect, url_for
from flask_login import login_required, current_user

from extensions import db, login_manager, scheduler

from config import Config
from models import User, Expense


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["PDF_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from auth import auth_bp
    from contractor import contractor_bp
    from admin import admin_bp
    from client import client_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(contractor_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(client_bp)

    from charts import ring_svg
    app.jinja_env.globals["ring_svg"] = ring_svg

    @app.context_processor
    def inject_pending_badge():
        """Sidebar badge: packets waiting for Matthew's approval."""
        if current_user.is_authenticated and current_user.is_admin():
            from models import WeeklyPacket
            n = WeeklyPacket.query.filter_by(approval_status="pending").count()
            return {"pending_badge": n or None}
        return {"pending_badge": None}

    @app.template_filter("local_time")
    def local_time(value, fmt="%b %-d, %-I:%M %p"):
        """Show a stored UTC timestamp in the business timezone (Pacific)."""
        from utils import to_local
        local = to_local(value)
        return local.strftime(fmt) if local else ""

    @app.template_filter("money")
    def money(value):
        try:
            return f"${float(value or 0):,.2f}"
        except (TypeError, ValueError):
            return "$0.00"

    @app.template_filter("hrs")
    def hrs(value):
        v = float(value or 0)
        return f"{v:g}" if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    @app.route("/uploads/<filename>")
    @login_required
    def uploaded_file(filename):
        # Only the admin, or the contractor who owns the expense this photo
        # belongs to, may view a receipt image.
        if not current_user.is_admin():
            expense = Expense.query.filter_by(photo_filename=filename).first()
            if not expense or expense.assignment.contractor_id != current_user.id:
                abort(403)
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    with app.app_context():
        db.create_all()
        from db_upgrade import run as run_db_upgrade
        run_db_upgrade(db)

    from scheduler_jobs import register_jobs
    register_jobs(app, scheduler)
    if not scheduler.running:
        scheduler.start()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
